from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import httpx
import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config import load_settings


DISCORD_API_BASE = "https://discord.com/api/v10"


@dataclass(frozen=True)
class DiscordMessage:
    content: str
    timestamp: str
    channel_id: str
    author_id: str
    author_name: str


@dataclass(frozen=True)
class PersonSeed:
    discord_id: str
    name: str
    notes: str


@dataclass(frozen=True)
class CachedMessages:
    messages: list[DiscordMessage]
    complete: bool


def parse_user_ids(values: list[str]) -> list[str]:
    user_ids: list[str] = []
    for value in values:
        for part in value.split(","):
            user_id = part.strip()
            if user_id:
                user_ids.append(user_id)
    seen: set[str] = set()
    deduped: list[str] = []
    for user_id in user_ids:
        if user_id in seen:
            continue
        if not user_id.isdigit():
            raise ValueError(f"Discord user ID must be numeric: {user_id}")
        seen.add(user_id)
        deduped.append(user_id)
    return deduped


def render_people_yaml(people: list[PersonSeed]) -> str:
    payload = {
        "people": [
            {
                "discord_id": person.discord_id,
                "name": person.name,
                "notes": person.notes,
            }
            for person in people
        ]
    }
    return yaml.safe_dump(payload, sort_keys=False, allow_unicode=False)


def message_cache_path(cache_dir: Path, guild_id: int, channel_id: int, user_id: str) -> Path:
    return cache_dir / str(guild_id) / str(channel_id) / f"{user_id}.json"


def message_from_dict(raw: dict[str, Any]) -> DiscordMessage:
    return DiscordMessage(
        content=str(raw.get("content") or ""),
        timestamp=str(raw.get("timestamp") or ""),
        channel_id=str(raw.get("channel_id") or ""),
        author_id=str(raw.get("author_id") or ""),
        author_name=str(raw.get("author_name") or raw.get("author_id") or ""),
    )


def load_cached_messages(
    cache_dir: Path,
    guild_id: int,
    channel_id: int,
    user_id: str,
    sample_limit: int,
) -> CachedMessages | None:
    path = message_cache_path(cache_dir, guild_id, channel_id, user_id)
    if not path.exists():
        return None

    payload = json.loads(path.read_text(encoding="utf-8"))
    messages = [message_from_dict(raw) for raw in payload.get("messages", [])]
    complete = bool(payload.get("complete"))
    if complete or len(messages) >= sample_limit:
        return CachedMessages(messages=messages[:sample_limit], complete=complete)
    return None


def save_cached_messages(
    cache_dir: Path,
    guild_id: int,
    channel_id: int,
    user_id: str,
    messages: list[DiscordMessage],
    complete: bool,
) -> None:
    path = message_cache_path(cache_dir, guild_id, channel_id, user_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "guild_id": str(guild_id),
        "channel_id": str(channel_id),
        "user_id": str(user_id),
        "complete": complete,
        "messages": [asdict(message) for message in messages],
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def extract_messages(payload: dict[str, Any], author_id: str) -> list[DiscordMessage]:
    messages: list[DiscordMessage] = []
    for group in payload.get("messages", []):
        for raw in group:
            author = raw.get("author") or {}
            if str(author.get("id", "")) != str(author_id):
                continue
            content = str(raw.get("content") or "").strip()
            if not content:
                continue
            display_name = (
                author.get("global_name")
                or author.get("username")
                or author.get("id")
                or author_id
            )
            messages.append(
                DiscordMessage(
                    content=content,
                    timestamp=str(raw.get("timestamp") or ""),
                    channel_id=str(raw.get("channel_id") or ""),
                    author_id=str(author.get("id") or author_id),
                    author_name=str(display_name),
                )
            )
    return messages


async def search_user_messages(
    client: httpx.AsyncClient,
    token: str,
    guild_id: int,
    channel_id: int,
    author_id: str,
    sample_limit: int,
    max_retries: int,
) -> list[DiscordMessage]:
    headers = {"Authorization": f"Bot {token}"}
    collected: list[DiscordMessage] = []
    seen_message_keys: set[tuple[str, str]] = set()

    for offset in range(0, min(9975, sample_limit * 4), 25):
        params: list[tuple[str, str | int]] = [
            ("limit", 25),
            ("offset", offset),
            ("channel_id", str(channel_id)),
            ("author_id", str(author_id)),
            ("author_type", "user"),
            ("sort_by", "timestamp"),
            ("sort_order", "desc"),
        ]

        for attempt in range(max_retries + 1):
            print(f"Searching messages for user {author_id} with offset {offset} (attempt {attempt + 1})...")
            print(f"Guild ID: {guild_id}, Channel ID: {channel_id}")
            response = await client.get(
                f"{DISCORD_API_BASE}/guilds/{guild_id}/messages/search",
                params=params,
                headers=headers,
            )
            if response.status_code == 202:
                retry_after = float(response.json().get("retry_after") or 1)
                if attempt >= max_retries:
                    raise RuntimeError(
                        f"Discord search index for user {author_id} was not ready after retries."
                    )
                await asyncio.sleep(max(retry_after, 1.0))
                continue
            response.raise_for_status()
            # Just print out the response...
            #print(f"Search response status: {response.status_code}")
            #print(f"Search response content: {response.text}")
            payload = response.json()
            break

        page_messages = extract_messages(payload, author_id)
        if not page_messages:
            break
        for message in page_messages:
            key = (message.timestamp, message.content)
            if key in seen_message_keys:
                continue
            seen_message_keys.add(key)
            collected.append(message)
            if len(collected) >= sample_limit:
                return collected
    return collected


def build_profile_prompt(user_id: str, messages: list[DiscordMessage]) -> str:
    sample = "\n".join(
        f"- [{message.timestamp}] {message.content.replace(chr(10), ' ')}"
        for message in messages
    )
    return f"""
Discord user ID: {user_id}
Discord user name: {messages[0].author_name if messages else user_id}
Message samples:
{sample if sample else "(no messages found)"}


You are generating private context notes for a Discord assistant.

Analyze the user's own messages and produce a concise interaction profile for how the assistant should treat and respond to them.

Rules:
- Use only evidence from the message samples.
- Focus on communication preferences, tone, humor, technical depth, likely interests, and response style.
- You MAY infer some personality traits or preferences based on the messages, but do not speculate wildly beyond the evidence.
- Prioritize recent messages if the sample is large.
- Mark uncertain conclusions as "seems" or "may".
- Do not quote private messages verbatim unless a tiny phrase is needed to describe tone.
- Output plain text notes only, suitable for a YAML literal block.
- Keep it under 220 words.
- DO NOT EVER ask followup questions. you MUST generate a profile (psychoanalysis) based on the provided messages alone, even if the sample is small or not very informative. Just do your best with the available data.

"""


async def generate_profile_with_ollama(
    ollama_base_url: str,
    model: str,
    user_id: str,
    messages: list[DiscordMessage],
) -> str:
    if not messages:
        return (
            "No searchable messages were found in the selected channel.\n"
            "TODO: add communication preferences manually."
        )

    payload = {
        "model": model,
        "messages": [{"role": "user", "content": build_profile_prompt(user_id, messages)}],
        "stream": False,
        "options": {"num_predict": 320, "temperature": 0.2},
    }
    async with httpx.AsyncClient(timeout=120) as client:
        response = await client.post(f"{ollama_base_url.rstrip('/')}/api/chat", json=payload)
        response.raise_for_status()
        data = response.json()
    return str((data.get("message") or {}).get("content") or "").strip()


async def fetch_people(
    token: str,
    guild_id: int,
    channel_id: int,
    user_ids: list[str],
    ollama_base_url: str,
    ollama_model: str,
    sample_limit: int,
    max_retries: int,
    message_cache_dir: Path | None,
    refresh_message_cache: bool,
) -> list[PersonSeed]:
    people: list[PersonSeed] = []
    async with httpx.AsyncClient(timeout=45) as client:
        for user_id in user_ids:
            cached = None
            if message_cache_dir is not None and not refresh_message_cache:
                cached = load_cached_messages(
                    cache_dir=message_cache_dir,
                    guild_id=guild_id,
                    channel_id=channel_id,
                    user_id=user_id,
                    sample_limit=sample_limit,
                )
            if cached is not None:
                messages = cached.messages
                print(f"Using {len(messages)} cached messages for user {user_id}.")
            else:
                messages = await search_user_messages(
                    client=client,
                    token=token,
                    guild_id=guild_id,
                    channel_id=channel_id,
                    author_id=user_id,
                    sample_limit=sample_limit,
                    max_retries=max_retries,
                )
                if message_cache_dir is not None:
                    save_cached_messages(
                        cache_dir=message_cache_dir,
                        guild_id=guild_id,
                        channel_id=channel_id,
                        user_id=user_id,
                        messages=messages,
                        complete=len(messages) < sample_limit,
                    )
                    print(f"Cached {len(messages)} messages for user {user_id}.")
            name = messages[0].author_name if messages else user_id
            profile = await generate_profile_with_ollama(
                ollama_base_url=ollama_base_url,
                model=ollama_model,
                user_id=user_id,
                messages=messages,
            )
            notes = (
                f"Generated from {len(messages)} searched messages in channel {channel_id}.\n"
                f"{profile}"
            )
            print(f"Generated profile for user {user_id} ({name}):\n{notes}\n")
            people.append(PersonSeed(discord_id=user_id, name=name, notes=notes))
    return people


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate personas/people.yaml by searching Discord messages and profiling with Ollama."
    )
    parser.add_argument("--guild-id", required=True, type=int, help="Discord server/guild ID.")
    parser.add_argument("--channel-id", required=True, type=int, help="Discord channel ID.")
    parser.add_argument(
        "--user-id",
        required=True,
        action="append",
        help="Discord user ID. Repeat the flag or pass comma-separated IDs.",
    )
    parser.add_argument(
        "--sample-limit",
        default=75,
        type=int,
        help="Max messages to sample per user. Defaults to 75.",
    )
    parser.add_argument(
        "--search-retries",
        default=3,
        type=int,
        help="Retries when Discord returns a 202 search-indexing response.",
    )
    parser.add_argument(
        "--output",
        default="personas/people.yaml",
        help="YAML file to write. Defaults to personas/people.yaml.",
    )
    parser.add_argument(
        "--message-cache-dir",
        default=".cache/generate_people_yaml/messages",
        help=(
            "Directory for cached Discord message samples. "
            "Defaults to .cache/generate_people_yaml/messages."
        ),
    )
    parser.add_argument(
        "--no-message-cache",
        action="store_true",
        help="Disable reading and writing cached Discord message samples.",
    )
    parser.add_argument(
        "--refresh-message-cache",
        action="store_true",
        help="Ignore existing cached messages and download fresh samples.",
    )
    parser.add_argument(
        "--env-file",
        default=".env",
        help="Path to .env file containing DISCORD_BOT_TOKEN and Ollama settings.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite the output file if it already exists.",
    )
    return parser


async def async_main() -> None:
    args = build_parser().parse_args()
    settings = load_settings(args.env_file)
    if not settings.discord_bot_token:
        raise SystemExit("DISCORD_BOT_TOKEN is required in the environment or .env file.")
    if args.sample_limit < 1:
        raise SystemExit("--sample-limit must be at least 1.")

    output = Path(args.output)
    if output.exists() and not args.force:
        raise SystemExit(f"{output} already exists. Re-run with --force to overwrite it.")

    user_ids = parse_user_ids(args.user_id)
    people = await fetch_people(
        token=settings.discord_bot_token,
        guild_id=args.guild_id,
        channel_id=args.channel_id,
        user_ids=user_ids,
        ollama_base_url=settings.ollama_base_url,
        ollama_model=settings.ollama_model,
        sample_limit=args.sample_limit,
        max_retries=args.search_retries,
        message_cache_dir=None if args.no_message_cache else Path(args.message_cache_dir),
        refresh_message_cache=args.refresh_message_cache,
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render_people_yaml(people), encoding="utf-8")
    print(f"Wrote {len(people)} people to {output}")


def main() -> None:
    asyncio.run(async_main())


if __name__ == "__main__":
    main()
