from __future__ import annotations

import argparse
import asyncio
import sqlite3
import sys
import time
from pathlib import Path
from dataclasses import dataclass
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent import AgentRequest, VioletAgent
from config import load_settings
from memory import MemoryStore, context_key_for_dm, context_key_for_guild
from personas import PeopleStore
from storage import Database


@dataclass
class ChatState:
    db: Database
    memory: MemoryStore
    people: PeopleStore
    agent: VioletAgent
    context_key_override: str | None
    guild_id: str
    channel_name: str
    server_name: str
    author_id: str
    author_name: str
    is_dm: bool

    def context_key(self) -> str:
        if self.context_key_override:
            return self.context_key_override
        return context_key_for_dm(self.author_id) if self.is_dm else context_key_for_guild(self.guild_id)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Chat with Violet using local personas and memory.")
    parser.add_argument("--db-path", default=None, help="Override DB path (defaults to settings).")
    parser.add_argument("--people-path", default=None, help="Override people.yaml path (defaults to settings).")
    parser.add_argument("--guild-id", default="demo-guild", help="Guild ID to emulate.")
    parser.add_argument("--channel-name", default="general", help="Channel name to emulate.")
    parser.add_argument("--server-name", default="Demo Server", help="Server name to emulate.")
    parser.add_argument("--author-id", default="1234567890", help="Author ID to emulate.")
    parser.add_argument("--author-name", default="You", help="Author name to emulate.")
    parser.add_argument("--dm", action="store_true", help="Use DM context key instead of guild.")
    parser.add_argument("--context-key", default=None, help="Explicit context key override.")
    parser.add_argument("--list-people", action="store_true", help="List people from people.yaml and exit.")
    return parser.parse_args()


def _find_person_by_name(people: PeopleStore, name: str) -> tuple[str, dict[str, str]] | None:
    target = name.strip().lower()
    for discord_id, entry in people.people.items():
        if str(entry.get("name", "")).strip().lower() == target:
            return discord_id, entry
    return None


def _render_memory(entries: Iterable[dict[str, Any]], limit: int | None = None) -> str:
    rows = list(entries)
    if limit is not None and limit > 0:
        rows = rows[-limit:]
    if not rows:
        return "(no memory entries)"
    lines: list[str] = []
    for entry in rows:
        ts = int(entry.get("ts") or 0)
        when = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts)) if ts else "?"
        role = str(entry.get("role") or "?")
        author = str(entry.get("author") or "?")
        channel = str(entry.get("channel") or "?")
        content = str(entry.get("content") or "")
        lines.append(f"[{when}] ({role}) #{channel} {author}: {content}")
    return "\n".join(lines)


def _print_context(state: ChatState) -> None:
    context_key = state.context_key()
    print("Context:")
    print(f"  context_key: {context_key}")
    print(f"  guild_id: {state.guild_id}")
    print(f"  server_name: {state.server_name}")
    print(f"  channel_name: {state.channel_name}")
    print(f"  author_id: {state.author_id}")
    print(f"  author_name: {state.author_name}")
    print(f"  is_dm: {state.is_dm}")


def _list_people(people: PeopleStore) -> None:
    if not people.people:
        print("No people found in people.yaml.")
        return
    print("People:")
    for discord_id, entry in people.people.items():
        name = entry.get("name") or "(no name)"
        print(f"  {discord_id}: {name}")


def _run_db_query(db_path: str, query: str) -> None:
    if not query.strip().lower().startswith(("select", "pragma")):
        print("Only SELECT or PRAGMA queries are allowed in /db query.")
        return
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(query).fetchall()
    finally:
        conn.close()
    if not rows:
        print("(no rows)")
        return
    headers = list(rows[0].keys())
    print(" | ".join(headers))
    print("-+-".join("-" * len(header) for header in headers))
    for row in rows:
        print(" | ".join(str(row[h]) for h in headers))


async def _chat_loop(state: ChatState) -> None:
    print("Type /help for commands. Ctrl+C to exit.")
    while True:
        try:
            raw = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nbye")
            return

        if not raw:
            continue

        if raw.startswith("/"):
            parts = raw[1:].split(maxsplit=2)
            command = parts[0].lower()
            arg1 = parts[1] if len(parts) > 1 else ""
            arg2 = parts[2] if len(parts) > 2 else ""

            if command in {"exit", "quit"}:
                print("bye")
                return
            if command == "help":
                print(
                    "Commands:\n"
                    "  /help\n"
                    "  /context\n"
                    "  /person list\n"
                    "  /person set <discord_id>\n"
                    "  /person name <name>\n"
                    "  /server <name>\n"
                    "  /channel <name>\n"
                    "  /guild <id>\n"
                    "  /dm on|off\n"
                    "  /context-key <key>\n"
                    "  /memory [limit]\n"
                    "  /memory add user <text>\n"
                    "  /memory add assistant <text>\n"
                    "  /db messages [limit]\n"
                    "  /db query <select...>"
                )
                continue
            if command == "context":
                _print_context(state)
                continue
            if command == "person":
                if arg1 == "list":
                    _list_people(state.people)
                elif arg1 == "set" and arg2:
                    entry = state.people.people.get(arg2)
                    if not entry:
                        print("Person not found.")
                        continue
                    state.author_id = arg2
                    state.author_name = entry.get("name") or state.author_name
                    print(f"Active person set to {state.author_name} ({state.author_id}).")
                elif arg1 == "name" and arg2:
                    match = _find_person_by_name(state.people, arg2)
                    if not match:
                        print("Person not found.")
                        continue
                    discord_id, entry = match
                    state.author_id = discord_id
                    state.author_name = entry.get("name") or state.author_name
                    print(f"Active person set to {state.author_name} ({state.author_id}).")
                else:
                    print("Usage: /person list | /person set <discord_id> | /person name <name>")
                continue
            if command == "server" and arg1:
                state.server_name = raw.split(" ", 1)[1].strip()
                print(f"server_name set to {state.server_name}")
                continue
            if command == "channel" and arg1:
                state.channel_name = raw.split(" ", 1)[1].strip()
                print(f"channel_name set to {state.channel_name}")
                continue
            if command == "guild" and arg1:
                state.guild_id = raw.split(" ", 1)[1].strip()
                print(f"guild_id set to {state.guild_id}")
                continue
            if command == "dm" and arg1:
                state.is_dm = arg1.lower() in {"1", "true", "yes", "on"}
                print(f"is_dm set to {state.is_dm}")
                continue
            if command == "context-key" and arg1:
                state.context_key_override = raw.split(" ", 1)[1].strip()
                print(f"context_key override set to {state.context_key_override}")
                continue
            if command == "memory":
                if arg1 == "add" and arg2:
                    if arg2.lower().startswith("assistant "):
                        content = arg2[len("assistant ") :].strip()
                        state.memory.append_assistant(
                            key=state.context_key(),
                            channel=state.channel_name,
                            content=content,
                        )
                        print("Assistant message added.")
                    elif arg2.lower().startswith("user "):
                        content = arg2[len("user ") :].strip()
                        state.memory.append(
                            key=state.context_key(),
                            channel=state.channel_name,
                            author=state.author_name,
                            author_id=state.author_id,
                            content=content,
                        )
                        print("User message added.")
                    else:
                        print("Usage: /memory add user <text> | /memory add assistant <text>")
                else:
                    limit = int(arg1) if arg1.isdigit() else None
                    print(_render_memory(state.memory.window(state.context_key()), limit))
                continue
            if command == "db":
                if arg1 == "messages":
                    limit = int(arg2) if arg2.isdigit() else 50
                    rows = state.db.load_recent_messages(state.context_key(), limit=limit)
                    print(_render_memory(rows, None))
                elif arg1 == "query" and arg2:
                    query = raw.split(" ", 2)[2].strip()
                    _run_db_query(state.db.path, query)
                else:
                    print("Usage: /db messages [limit] | /db query <select...>")
                continue

            print("Unknown command. Type /help.")
            continue

        state.memory.append(
            key=state.context_key(),
            channel=state.channel_name,
            author=state.author_name,
            author_id=state.author_id,
            content=raw,
        )
        snapshot = state.memory.get_snapshot(state.context_key())
        response = await state.agent.generate(
            AgentRequest(
                content=raw,
                author_name=state.author_name,
                author_id=state.author_id,
                context_key=state.context_key(),
                channel_name=state.channel_name,
                guild_id=state.context_key() if state.is_dm else state.guild_id,
                server_name=state.server_name,
                is_dm=state.is_dm,
                context_snapshot=snapshot,
            )
        )
        print(response.text)
        if response.text.strip():
            state.memory.append_assistant(
                key=state.context_key(),
                channel=state.channel_name,
                content=response.text,
            )


async def main() -> None:
    args = _parse_args()
    settings = load_settings()
    db_path = args.db_path or settings.db_path
    people_path = args.people_path or settings.people_path

    db = Database(db_path)
    db.init()
    memory = MemoryStore(db, settings.context_token_budget)
    people = PeopleStore.load(people_path)
    agent = VioletAgent(memory=memory, people=people, db=db, config=settings)

    if args.list_people:
        _list_people(people)
        return

    author_id = args.author_id
    author_name = args.author_name
    entry = people.people.get(author_id)
    if entry and entry.get("name"):
        author_name = entry["name"]

    state = ChatState(
        db=db,
        memory=memory,
        people=people,
        agent=agent,
        context_key_override=args.context_key,
        guild_id=args.guild_id,
        channel_name=args.channel_name,
        server_name=args.server_name,
        author_id=author_id,
        author_name=author_name,
        is_dm=args.dm,
    )
    _print_context(state)
    await _chat_loop(state)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("bye")
        sys.exit(0)
