from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import httpx
import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config import load_settings


DISCORD_API_BASE = "https://discord.com/api/v10"
CHANNEL_PAGE_LIMIT = 100
DEFAULT_MAX_MESSAGES = 10000
DEFAULT_LLM_MODEL = "gemini-3-flash-preview"
GOOGLE_OPENAI_API_BASE = "https://generativelanguage.googleapis.com/v1beta/openai/"
GOOGLE_TIMEOUT_SECONDS = 120.0
DEFAULT_PROFILE_CACHE_DIR = ".cache/generate_people_yaml_history/profiles"
DEFAULT_INTERACTION_CACHE_DIR = ".cache/generate_people_yaml_history/interactions"


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
    voice: str


@dataclass(frozen=True)
class CachedChannelManifest:
    complete: bool
    total_messages: int
    max_messages: int
    last_page_index: int
    next_before: str


@dataclass(frozen=True)
class CachedChannelHistory:
    messages: list[DiscordMessage]
    manifest: CachedChannelManifest


@dataclass(frozen=True)
class CachedProfile:
    profile: str
    voice: str
    profile_prompt_hash: str
    voice_prompt_hash: str
    model: str


@dataclass(frozen=True)
class CachedInteractionNotes:
    a_to_b: str
    b_to_a: str
    prompt_hash: str
    model: str


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
                "voice": person.voice,
            }
            for person in people
        ]
    }
    return yaml.safe_dump(payload, sort_keys=False, allow_unicode=False)


def render_interactions_yaml(interactions: list[dict[str, Any]]) -> str:
    payload = {"interactions": interactions}
    return yaml.safe_dump(payload, sort_keys=False, allow_unicode=False)


def channel_cache_dir(base_dir: Path, guild_id: int, channel_id: int) -> Path:
    return base_dir / str(guild_id) / str(channel_id)


def channel_manifest_path(base_dir: Path, guild_id: int, channel_id: int) -> Path:
    return channel_cache_dir(base_dir, guild_id, channel_id) / "manifest.json"


def channel_page_path(base_dir: Path, guild_id: int, channel_id: int, page_index: int, before_id: str) -> Path:
    before_slug = before_id if before_id else "start"
    filename = f"page_{page_index:05d}_before_{before_slug}.json"
    return channel_cache_dir(base_dir, guild_id, channel_id) / filename


def profile_cache_path(base_dir: Path, guild_id: int, channel_id: int, user_id: str) -> Path:
    return base_dir / str(guild_id) / str(channel_id) / f"{user_id}.json"


def interaction_cache_path(
    base_dir: Path, guild_id: int, channel_id: int, user_a: str, user_b: str
) -> Path:
    filename = f"{user_a}_{user_b}.json"
    return base_dir / str(guild_id) / str(channel_id) / filename


def parse_timestamp(value: str) -> datetime:
    if not value:
        return datetime.fromtimestamp(0, tz=timezone.utc)
    cleaned = value.strip()
    if cleaned.endswith("Z"):
        cleaned = f"{cleaned[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(cleaned)
    except ValueError:
        return datetime.fromtimestamp(0, tz=timezone.utc)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def message_from_dict(raw: dict[str, Any]) -> DiscordMessage:
    return DiscordMessage(
        content=str(raw.get("content") or ""),
        timestamp=str(raw.get("timestamp") or ""),
        channel_id=str(raw.get("channel_id") or ""),
        author_id=str(raw.get("author_id") or ""),
        author_name=str(raw.get("author_name") or raw.get("author_id") or ""),
    )


def extract_messages_from_channel(payload: list[dict[str, Any]]) -> list[DiscordMessage]:
    messages: list[DiscordMessage] = []
    for raw in payload:
        author = raw.get("author") or {}
        content = str(raw.get("content") or "").strip()
        if not content:
            continue
        author_id = str(author.get("id") or "")
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
                author_id=author_id,
                author_name=str(display_name),
            )
        )
    return messages


def dedupe_messages(messages: Iterable[DiscordMessage]) -> list[DiscordMessage]:
    seen: set[tuple[str, str, str]] = set()
    deduped: list[DiscordMessage] = []
    for message in messages:
        key = (message.timestamp, message.content, message.author_id)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(message)
    return deduped


def prompt_cache_hash(prompt: str, model: str) -> str:
    digest = hashlib.sha256()
    digest.update(model.encode("utf-8"))
    digest.update(b"\n")
    digest.update(prompt.encode("utf-8"))
    return digest.hexdigest()


def load_cached_profile(
    cache_dir: Path,
    guild_id: int,
    channel_id: int,
    user_id: str,
    profile_prompt_hash: str,
    voice_prompt_hash: str,
    model: str,
) -> CachedProfile | None:
    path = profile_cache_path(cache_dir, guild_id, channel_id, user_id)
    if not path.exists():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    if str(payload.get("model") or "") != model:
        return None
    if str(payload.get("profile_prompt_hash") or "") != profile_prompt_hash:
        return None
    if str(payload.get("voice_prompt_hash") or "") != voice_prompt_hash:
        return None
    return CachedProfile(
        profile=str(payload.get("profile") or ""),
        voice=str(payload.get("voice") or ""),
        profile_prompt_hash=profile_prompt_hash,
        voice_prompt_hash=voice_prompt_hash,
        model=model,
    )


def save_cached_profile(
    cache_dir: Path,
    guild_id: int,
    channel_id: int,
    user_id: str,
    profile: str,
    voice: str,
    profile_prompt_hash: str,
    voice_prompt_hash: str,
    model: str,
) -> None:
    path = profile_cache_path(cache_dir, guild_id, channel_id, user_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "guild_id": str(guild_id),
        "channel_id": str(channel_id),
        "user_id": str(user_id),
        "model": model,
        "profile_prompt_hash": profile_prompt_hash,
        "voice_prompt_hash": voice_prompt_hash,
        "profile": profile,
        "voice": voice,
        "generated_at": int(datetime.now(tz=timezone.utc).timestamp()),
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def load_cached_interaction_notes(
    cache_dir: Path,
    guild_id: int,
    channel_id: int,
    user_a: str,
    user_b: str,
    prompt_hash: str,
    model: str,
) -> CachedInteractionNotes | None:
    path = interaction_cache_path(cache_dir, guild_id, channel_id, user_a, user_b)
    if not path.exists():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    if str(payload.get("model") or "") != model:
        return None
    if str(payload.get("prompt_hash") or "") != prompt_hash:
        return None
    return CachedInteractionNotes(
        a_to_b=str(payload.get("a_to_b") or ""),
        b_to_a=str(payload.get("b_to_a") or ""),
        prompt_hash=prompt_hash,
        model=model,
    )


def save_cached_interaction_notes(
    cache_dir: Path,
    guild_id: int,
    channel_id: int,
    user_a: str,
    user_b: str,
    a_to_b: str,
    b_to_a: str,
    prompt_hash: str,
    model: str,
) -> None:
    path = interaction_cache_path(cache_dir, guild_id, channel_id, user_a, user_b)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "guild_id": str(guild_id),
        "channel_id": str(channel_id),
        "user_a": str(user_a),
        "user_b": str(user_b),
        "model": model,
        "prompt_hash": prompt_hash,
        "a_to_b": a_to_b,
        "b_to_a": b_to_a,
        "generated_at": int(datetime.now(tz=timezone.utc).timestamp()),
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def save_channel_page(
    cache_dir: Path,
    guild_id: int,
    channel_id: int,
    page_index: int,
    before_id: str,
    next_before: str,
    messages: list[DiscordMessage],
) -> None:
    channel_dir = channel_cache_dir(cache_dir, guild_id, channel_id)
    channel_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "guild_id": str(guild_id),
        "channel_id": str(channel_id),
        "page_index": page_index,
        "before_id": before_id,
        "next_before": next_before,
        "messages": [asdict(message) for message in messages],
    }
    channel_page_path(cache_dir, guild_id, channel_id, page_index, before_id).write_text(
        json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8"
    )


def save_channel_manifest(
    cache_dir: Path,
    guild_id: int,
    channel_id: int,
    complete: bool,
    total_messages: int,
    max_messages: int,
    last_page_index: int,
    next_before: str,
) -> None:
    payload = {
        "guild_id": str(guild_id),
        "channel_id": str(channel_id),
        "complete": complete,
        "total_messages": total_messages,
        "max_messages": max_messages,
        "last_page_index": last_page_index,
        "next_before": next_before,
        "generated_at": int(datetime.now(tz=timezone.utc).timestamp()),
    }
    channel_manifest_path(cache_dir, guild_id, channel_id).write_text(
        json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8"
    )


def load_cached_channel_history(
    cache_dir: Path,
    guild_id: int,
    channel_id: int,
    max_messages: int,
) -> CachedChannelHistory | None:
    manifest_file = channel_manifest_path(cache_dir, guild_id, channel_id)
    if not manifest_file.exists():
        return None
    payload = json.loads(manifest_file.read_text(encoding="utf-8"))
    complete = bool(payload.get("complete"))
    cached_total = int(payload.get("total_messages") or 0)
    cached_max = int(payload.get("max_messages") or 0)
    last_page_index = int(payload.get("last_page_index") or 0)
    next_before = str(payload.get("next_before") or "")

    if max_messages > 0 and cached_max and cached_max < max_messages:
        max_messages = cached_max

    channel_dir = channel_cache_dir(cache_dir, guild_id, channel_id)
    messages: list[DiscordMessage] = []
    for page_file in sorted(channel_dir.glob("page_*.json")):
        page_payload = json.loads(page_file.read_text(encoding="utf-8"))
        for raw in page_payload.get("messages", []):
            messages.append(message_from_dict(raw))
    deduped = dedupe_messages(messages)
    ordered = sorted(deduped, key=lambda msg: parse_timestamp(msg.timestamp), reverse=True)
    trimmed = list(reversed(ordered if max_messages <= 0 else ordered[:max_messages]))
    return CachedChannelHistory(
        messages=trimmed,
        manifest=CachedChannelManifest(
            complete=complete,
            total_messages=cached_total,
            max_messages=max_messages,
            last_page_index=last_page_index,
            next_before=next_before,
        ),
    )


def build_profile_prompt(user_id: str, user_name: str, messages: list[DiscordMessage]) -> str:
    sample = "\n".join(
        f"- [{message.timestamp}] {message.content.replace(chr(10), ' ')}"
        for message in messages
    )
    return f"""
Discord user ID: {user_id}
Discord user name (may be stale): {user_name}
Message samples (by this user only):
{sample if sample else "(no messages found)"}


You are generating private context notes for a Discord assistant.

Analyze the user's own messages and produce a concise interaction profile for how the assistant should treat and respond to them.

Rules:
- Use only evidence from the message samples.
- Focus on communication preferences, tone, humor, technical depth, likely interests, and response style.
- Include how they talk in general (pace, directness, humor, formality, etc.).
- You MAY infer some personality traits or preferences based on the messages, but do not speculate wildly beyond the evidence.
- Prioritize recent messages if the sample is large.
- Mark uncertain conclusions as "seems" or "may".
- Do not quote private messages verbatim unless a tiny phrase is needed to describe tone.
- Output plain text notes only, suitable for a YAML literal block.
- Keep it under 220 words.
- Refer to the user only by Discord ID (not by name).
- DO NOT EVER ask followup questions. You MUST generate a profile based on the provided messages alone.

"""


def build_voice_prompt(user_id: str, user_name: str, messages: list[DiscordMessage]) -> str:
    sample = "\n".join(
        f"- [{message.timestamp}] {message.content.replace(chr(10), ' ')}"
        for message in messages
    )
    return f"""
Discord user ID: {user_id}
Discord user name (may be stale): {user_name}
Message samples (by this user only):
{sample if sample else "(no messages found)"}


Create a concise prompt that can be used to answer questions in the voice of this user.

Rules:
- Base it only on the message samples.
- Capture tone, brevity, humor, vocabulary, and typical structure.
- Keep it under 120 words.
- Refer to the user only by Discord ID (not by name).
- Output plain text only (no JSON or Markdown).
"""


def build_interaction_prompt(
    user_a: str,
    user_b: str,
    messages: list[DiscordMessage],
) -> str:
    sample = "\n".join(
        f"- [{message.timestamp}] {message.author_id}: {message.content.replace(chr(10), ' ')}"
        for message in messages
    )
    return f"""
Discord user A ID: {user_a}
Discord user B ID: {user_b}
Transcript snippets (only these two users):
{sample if sample else "(no messages found)"}


You are summarizing interpersonal interaction patterns between two Discord users.

Rules:
- Use only evidence from the transcript snippets.
- Describe how A treats B and how B treats A.
- Focus on tone, helpfulness, patience, conflict, teasing, collaboration, and respect.
- Keep each direction under 120 words.
- Refer to both users only by Discord ID (not by name).
- Output strict JSON with keys "a_to_b" and "b_to_a".
"""


async def fetch_channel_history(
    client: httpx.AsyncClient,
    token: str,
    guild_id: int,
    channel_id: int,
    max_messages: int,
    max_retries: int,
    cache_dir: Path | None,
    refresh_cache: bool,
) -> tuple[list[DiscordMessage], bool]:
    headers = {"Authorization": f"Bot {token}"}
    collected: list[DiscordMessage] = []
    seen_message_keys: set[tuple[str, str]] = set()

    page_index = 0
    next_before = ""
    if cache_dir is not None and not refresh_cache:
        cached = load_cached_channel_history(
            cache_dir=cache_dir,
            guild_id=guild_id,
            channel_id=channel_id,
            max_messages=max_messages,
        )
        if cached is not None:
            collected = cached.messages
            page_index = cached.manifest.last_page_index + 1
            next_before = cached.manifest.next_before
            print(
                "Cache hit for channel history: "
                f"messages={len(collected)} complete={cached.manifest.complete} "
                f"next_before={next_before or 'start'}"
            )
            if cached.manifest.complete:
                print(f"Using cached channel history with {len(collected)} messages.")
                return collected, True
            if max_messages > 0 and len(collected) >= max_messages:
                print(f"Using cached channel history with {len(collected)} messages.")
                return collected, False
    else:
        if cache_dir is None:
            print("Message cache disabled.")
        else:
            print("Refresh cache requested; ignoring cached pages.")

    complete = False
    print(
        f"Fetching channel history (limit={max_messages or 'all'}, "
        f"page_size={CHANNEL_PAGE_LIMIT})..."
    )
    while True:
        if max_messages > 0 and len(collected) >= max_messages:
            break
        params: dict[str, str | int] = {"limit": CHANNEL_PAGE_LIMIT}
        if next_before:
            params["before"] = next_before

        for attempt in range(max_retries + 1):
            response = await client.get(
                f"{DISCORD_API_BASE}/channels/{channel_id}/messages",
                params=params,
                headers=headers,
            )
            if response.status_code == 429:
                retry_after = float(response.json().get("retry_after") or 1)
                print(
                    f"Rate limited (429). Sleeping {retry_after:.2f}s "
                    f"(attempt {attempt + 1}/{max_retries + 1})."
                )
                await asyncio.sleep(max(retry_after, 1.0))
                continue
            response.raise_for_status()
            payload = response.json()
            break

        if not payload:
            complete = True
            break

        page_messages = extract_messages_from_channel(payload)
        print(
            f"Fetched page {page_index} with {len(page_messages)} messages "
            f"(before={next_before or 'start'})."
        )
        for message in page_messages:
            key = (message.timestamp, message.content)
            if key in seen_message_keys:
                continue
            seen_message_keys.add(key)
            collected.append(message)
        oldest = str(payload[-1].get("id") or "")
        if cache_dir is not None:
            save_channel_page(
                cache_dir=cache_dir,
                guild_id=guild_id,
                channel_id=channel_id,
                page_index=page_index,
                before_id=next_before,
                next_before=oldest,
                messages=page_messages,
            )
            print(f"Cached page {page_index} with {len(page_messages)} messages.")
        next_before = oldest
        page_index += 1
        if not oldest:
            complete = True
            break

    if cache_dir is not None:
        save_channel_manifest(
            cache_dir=cache_dir,
            guild_id=guild_id,
            channel_id=channel_id,
            complete=complete,
            total_messages=len(collected),
            max_messages=max_messages,
            last_page_index=max(page_index - 1, 0),
            next_before=next_before,
        )
        print(
            f"Saved channel manifest: messages={len(collected)} complete={complete} "
            f"next_before={next_before or 'start'}"
        )
    return collected, complete


def openai_chat_url(api_base: str) -> str:
    base = api_base.rstrip("/")
    if base.endswith("/v1/chat/completions"):
        return base
    if base.endswith("/v1"):
        return f"{base}/chat/completions"
    return f"{base}/v1/chat/completions"


async def openai_chat_completion(
    api_base: str,
    api_key: str,
    model: str,
    messages: list[dict[str, str]],
    temperature: float,
    max_tokens: int,
) -> str:
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    
    async def call_llm(base_url: str, timeout_seconds: float) -> httpx.Response:
        async with httpx.AsyncClient(timeout=timeout_seconds) as client:
            print(
                f"Calling LLM model={model} base_url={base_url} "
                f"messages={len(messages)} max_tokens={max_tokens} temp={temperature}"
            )
            return await client.post(
                openai_chat_url(base_url),
                json=payload,
                headers=headers,
            )

    is_google_base = api_base.rstrip("/") == GOOGLE_OPENAI_API_BASE.rstrip("/")
    timeout_seconds = GOOGLE_TIMEOUT_SECONDS if is_google_base else 120.0
    response = await call_llm(api_base, timeout_seconds)
    if response.status_code == 400 and not is_google_base:
        print("Local LLM returned 400; retrying with Google API base URL.")
        response = await call_llm(GOOGLE_OPENAI_API_BASE, GOOGLE_TIMEOUT_SECONDS)
    if response.status_code != 200:
        print(f"LLM API error {response.status_code}: {response.text}")
        response.raise_for_status()
    data = response.json()
    choices = data.get("choices") or []
    if not choices:
        return ""
    message = choices[0].get("message") or {}
    print(f"LLM response: {str(message)[:200]}...")
    return str(message.get("content") or "").strip()


async def generate_profile_with_llm(
    api_base: str,
    api_key: str,
    model: str,
    user_id: str,
    user_name: str,
    messages: list[DiscordMessage],
) -> str:
    if not messages:
        return (
            f"No searchable messages were found for Discord ID {user_id}.\n"
            "TODO: add communication preferences manually."
        )

    return await openai_chat_completion(
        api_base=api_base,
        api_key=api_key,
        model=model,
        messages=[{"role": "user", "content": build_profile_prompt(user_id, user_name, messages)}],
        temperature=0.2,
        max_tokens=360,
    )


async def generate_voice_with_llm(
    api_base: str,
    api_key: str,
    model: str,
    user_id: str,
    user_name: str,
    messages: list[DiscordMessage],
) -> str:
    if not messages:
        return f"Answer as Discord ID {user_id} with their known preferences."

    return await openai_chat_completion(
        api_base=api_base,
        api_key=api_key,
        model=model,
        messages=[{"role": "user", "content": build_voice_prompt(user_id, user_name, messages)}],
        temperature=0.3,
        max_tokens=220,
    )


async def generate_interaction_with_llm(
    api_base: str,
    api_key: str,
    model: str,
    user_a: str,
    user_b: str,
    messages: list[DiscordMessage],
) -> tuple[str, str]:
    if not messages:
        return "", ""

    content = await openai_chat_completion(
        api_base=api_base,
        api_key=api_key,
        model=model,
        messages=[{"role": "user", "content": build_interaction_prompt(user_a, user_b, messages)}],
        temperature=0.2,
        max_tokens=300,
    )
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        return content, ""
    a_to_b = str(parsed.get("a_to_b") or "").strip()
    b_to_a = str(parsed.get("b_to_a") or "").strip()
    return a_to_b, b_to_a


def sample_recent_messages(messages: list[DiscordMessage], limit: int) -> list[DiscordMessage]:
    if limit <= 0:
        return list(messages)
    ordered = sorted(messages, key=lambda msg: parse_timestamp(msg.timestamp), reverse=True)
    return list(reversed(ordered[:limit]))


def merge_pair_messages(
    messages_by_user: dict[str, list[DiscordMessage]],
    user_a: str,
    user_b: str,
) -> list[DiscordMessage]:
    combined = list(messages_by_user.get(user_a, [])) + list(messages_by_user.get(user_b, []))
    return sorted(dedupe_messages(combined), key=lambda msg: parse_timestamp(msg.timestamp))


def filter_messages_by_user(messages: list[DiscordMessage], user_id: str) -> list[DiscordMessage]:
    return [message for message in messages if message.author_id == user_id]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Generate personas/people.yaml from full channel history, plus interaction logs."
        )
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
        "--max-messages",
        default=DEFAULT_MAX_MESSAGES,
        type=int,
        help=(
            "Max messages to fetch from the channel. 0 means no limit (fetch all)."
        ),
    )
    parser.add_argument(
        "--profile-sample-limit",
        default=0,
        type=int,
        help="Max messages per user to include in profile prompt. 0 means all.",
    )
    parser.add_argument(
        "--interaction-sample-limit",
        default=0,
        type=int,
        help="Max pairwise messages to include in interaction prompt. 0 means all.",
    )
    parser.add_argument(
        "--interaction-min-messages",
        default=10,
        type=int,
        help="Minimum pairwise messages required before generating interaction notes.",
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
        "--interaction-output",
        default="personas/interaction_notes.yaml",
        help="YAML file to write interaction notes.",
    )
    parser.add_argument(
        "--message-cache-dir",
        default=".cache/generate_people_yaml_history/channel_messages",
        help="Directory for cached channel history pages.",
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
        "--profile-cache-dir",
        default=DEFAULT_PROFILE_CACHE_DIR,
        help="Directory for cached LLM-generated profiles/voices.",
    )
    parser.add_argument(
        "--no-profile-cache",
        action="store_true",
        help="Disable reading and writing cached LLM-generated profiles/voices.",
    )
    parser.add_argument(
        "--refresh-profile-cache",
        action="store_true",
        help="Ignore existing cached profiles/voices and regenerate them.",
    )
    parser.add_argument(
        "--interaction-cache-dir",
        default=DEFAULT_INTERACTION_CACHE_DIR,
        help="Directory for cached interaction notes.",
    )
    parser.add_argument(
        "--no-interaction-cache",
        action="store_true",
        help="Disable reading and writing cached interaction notes.",
    )
    parser.add_argument(
        "--refresh-interaction-cache",
        action="store_true",
        help="Ignore existing cached interaction notes and regenerate them.",
    )
    parser.add_argument(
        "--env-file",
        default=".env",
        help="Path to .env file containing DISCORD_BOT_TOKEN and LLM settings.",
    )
    parser.add_argument(
        "--llm-base-url",
        default=os.getenv("OPENAI_API_BASE", "http://localhost:8080/v1"),
        help="OpenAI-compatible base URL for the LLM server.",
    )
    parser.add_argument(
        "--llm-api-key",
        default=os.getenv("OPENAI_API_KEY", "AIzaSyAHGAFSjYHiQLORlt8QH4i3kfZaPlsSyvc"),
        help="API key for the LLM server (optional for local servers).",
    )
    parser.add_argument(
        "--llm-model",
        default=os.getenv("OPENAI_MODEL", DEFAULT_LLM_MODEL),
        help="Model name for the LLM server.",
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
    if args.max_messages < 0:
        raise SystemExit("--max-messages must be 0 or greater.")

    output = Path(args.output)
    if output.exists() and not args.force:
        raise SystemExit(f"{output} already exists. Re-run with --force to overwrite it.")

    interaction_output = Path(args.interaction_output)
    if interaction_output.exists() and not args.force:
        raise SystemExit(
            f"{interaction_output} already exists. Re-run with --force to overwrite it."
        )

    print(f"Generating people.yaml from channel {args.channel_id} in guild {args.guild_id}...")

    user_ids = parse_user_ids(args.user_id)
    cache_dir = None if args.no_message_cache else Path(args.message_cache_dir)
    profile_cache_dir = None if args.no_profile_cache else Path(args.profile_cache_dir)
    interaction_cache_dir = (
        None if args.no_interaction_cache else Path(args.interaction_cache_dir)
    )

    async with httpx.AsyncClient(timeout=45) as client:
        channel_messages, channel_complete = await fetch_channel_history(
            client=client,
            token=settings.discord_bot_token,
            guild_id=args.guild_id,
            channel_id=args.channel_id,
            max_messages=args.max_messages,
            max_retries=args.search_retries,
            cache_dir=cache_dir,
            refresh_cache=args.refresh_message_cache,
        )

    messages_by_user = {
        user_id: filter_messages_by_user(channel_messages, user_id) for user_id in user_ids
    }
    print(
        "Per-user message counts: "
        + ", ".join(
            f"{user_id}={len(messages_by_user.get(user_id, []))}" for user_id in user_ids
        )
    )

    people: list[PersonSeed] = []
    for user_id in user_ids:
        messages = messages_by_user.get(user_id, [])
        sample = sample_recent_messages(messages, args.profile_sample_limit)
        user_name = sample[-1].author_name if sample else user_id
        print(
            f"Generating profile/voice for user {user_id} "
            f"(sample_size={len(sample)} name_hint={user_name})."
        )
        if not sample:
            profile = (
                f"No searchable messages were found for Discord ID {user_id}.\n"
                "TODO: add communication preferences manually."
            )
            voice = f"Answer as Discord ID {user_id} with their known preferences."
        else:
            profile_prompt = build_profile_prompt(user_id, user_name, sample)
            voice_prompt = build_voice_prompt(user_id, user_name, sample)
            profile_prompt_hash = prompt_cache_hash(profile_prompt, args.llm_model)
            voice_prompt_hash = prompt_cache_hash(voice_prompt, args.llm_model)
            cached_profile = None
            if profile_cache_dir is not None and not args.refresh_profile_cache:
                cached_profile = load_cached_profile(
                    cache_dir=profile_cache_dir,
                    guild_id=args.guild_id,
                    channel_id=args.channel_id,
                    user_id=user_id,
                    profile_prompt_hash=profile_prompt_hash,
                    voice_prompt_hash=voice_prompt_hash,
                    model=args.llm_model,
                )
            if cached_profile is not None:
                print(f"Using cached profile/voice for user {user_id}.")
                profile = cached_profile.profile
                voice = cached_profile.voice
            else:
                profile = await openai_chat_completion(
                    api_base=args.llm_base_url,
                    api_key=args.llm_api_key,
                    model=args.llm_model,
                    messages=[{"role": "user", "content": profile_prompt}],
                    temperature=0.2,
                    max_tokens=360,
                )
                voice = await openai_chat_completion(
                    api_base=args.llm_base_url,
                    api_key=args.llm_api_key,
                    model=args.llm_model,
                    messages=[{"role": "user", "content": voice_prompt}],
                    temperature=0.3,
                    max_tokens=220,
                )
                if profile_cache_dir is not None:
                    save_cached_profile(
                        cache_dir=profile_cache_dir,
                        guild_id=args.guild_id,
                        channel_id=args.channel_id,
                        user_id=user_id,
                        profile=profile,
                        voice=voice,
                        profile_prompt_hash=profile_prompt_hash,
                        voice_prompt_hash=voice_prompt_hash,
                        model=args.llm_model,
                    )
        completion_note = "complete" if channel_complete else "partial"
        notes = (
            f"Generated from {len(messages)} messages in channel {args.channel_id} "
            f"({completion_note} history).\n"
            f"{profile}"
        )
        print(f"Generated profile for user {user_id} ({user_name}).")
        people.append(PersonSeed(discord_id=user_id, name=user_name, notes=notes, voice=voice))

    # interactions: list[dict[str, Any]] = []
    # for index, user_a in enumerate(user_ids):
    #     for user_b in user_ids[index + 1 :]:
    #         merged = merge_pair_messages(messages_by_user, user_a, user_b)
    #         if len(merged) < args.interaction_min_messages:
    #             print(
    #                 f"Skipping interaction notes for {user_a} and {user_b} "
    #                 f"(only {len(merged)} messages)."
    #             )
    #             continue
    #         sample = sample_recent_messages(merged, args.interaction_sample_limit)
    #         print(
    #             f"Generating interaction notes for {user_a} <-> {user_b} "
    #             f"(sample_size={len(sample)})."
    #         )
    #         interaction_prompt = build_interaction_prompt(user_a, user_b, sample)
    #         interaction_prompt_hash = prompt_cache_hash(
    #             interaction_prompt, args.llm_model
    #         )
    #         cached_notes = None
    #         if interaction_cache_dir is not None and not args.refresh_interaction_cache:
    #             cached_notes = load_cached_interaction_notes(
    #                 cache_dir=interaction_cache_dir,
    #                 guild_id=args.guild_id,
    #                 channel_id=args.channel_id,
    #                 user_a=user_a,
    #                 user_b=user_b,
    #                 prompt_hash=interaction_prompt_hash,
    #                 model=args.llm_model,
    #             )
    #         if cached_notes is not None:
    #             print(f"Using cached interaction notes for {user_a} <-> {user_b}.")
    #             a_to_b = cached_notes.a_to_b
    #             b_to_a = cached_notes.b_to_a
    #         else:
    #             a_to_b, b_to_a = await generate_interaction_with_llm(
    #                 api_base=args.llm_base_url,
    #                 api_key=args.llm_api_key,
    #                 model=args.llm_model,
    #                 user_a=user_a,
    #                 user_b=user_b,
    #                 messages=sample,
    #             )
    #             if interaction_cache_dir is not None:
    #                 save_cached_interaction_notes(
    #                     cache_dir=interaction_cache_dir,
    #                     guild_id=args.guild_id,
    #                     channel_id=args.channel_id,
    #                     user_a=user_a,
    #                     user_b=user_b,
    #                     a_to_b=a_to_b,
    #                     b_to_a=b_to_a,
    #                     prompt_hash=interaction_prompt_hash,
    #                     model=args.llm_model,
    #                 )
    #         if a_to_b:
    #             interactions.append(
    #                 {
    #                     "from_id": user_a,
    #                     "to_id": user_b,
    #                     "notes": a_to_b,
    #                     "sample_size": len(sample),
    #                 }
    #             )
    #         if b_to_a:
    #             interactions.append(
    #                 {
    #                     "from_id": user_b,
    #                     "to_id": user_a,
    #                     "notes": b_to_a,
    #                     "sample_size": len(sample),
    #                 }
    #             )
    #         print(f"Generated interaction notes for {user_a} <-> {user_b}.")

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render_people_yaml(people), encoding="utf-8")
    print(f"Wrote {len(people)} people to {output}")

    interactions = []
    #interaction_output.parent.mkdir(parents=True, exist_ok=True)
    #interaction_output.write_text(render_interactions_yaml(interactions), encoding="utf-8")
    print(f"Wrote {len(interactions)} interactions to {interaction_output}")


def main() -> None:
    asyncio.run(async_main())


if __name__ == "__main__":
    main()
