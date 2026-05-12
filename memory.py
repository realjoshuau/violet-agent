from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from storage import Database


def rough_token_count(text: str) -> int:
    return max(1, len(text) // 4)


def context_key_for_guild(guild_id: int | str) -> str:
    return str(guild_id)


def context_key_for_dm(user_id: int | str) -> str:
    return f"dm_{user_id}"


@dataclass
class MemoryEntry:
    channel: str
    author: str
    author_id: str
    content: str
    ts: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "channel": self.channel,
            "author": self.author,
            "author_id": self.author_id,
            "content": self.content,
            "ts": self.ts,
        }


class MemoryStore:
    def __init__(self, db: Database, token_budget: int) -> None:
        self.db = db
        self.token_budget = token_budget
        self.context_store: dict[str, list[dict[str, Any]]] = {}

    def hydrate(self, key: str, limit: int = 200) -> None:
        self.context_store[key] = self._trim(self.db.load_recent_messages(key, limit))

    def append(
        self,
        key: str,
        channel: str,
        author: str,
        author_id: str,
        content: str,
        ts: int | None = None,
        persist: bool = True,
    ) -> None:
        entry = MemoryEntry(
            channel=channel,
            author=author,
            author_id=str(author_id),
            content=content,
            ts=ts or int(time.time()),
        ).as_dict()
        bucket = self.context_store.setdefault(key, [])
        bucket.append(entry)
        self.context_store[key] = self._trim(bucket)
        if persist:
            self.db.insert_message(
                guild_id=key,
                channel_name=channel,
                author=author,
                author_id=str(author_id),
                content=content,
                ts=entry["ts"],
            )

    def window(self, key: str) -> list[dict[str, Any]]:
        if key not in self.context_store:
            print(f"[MS] Hydrating memory for key {key}")
            self.hydrate(key)
        return list(self.context_store.get(key, []))

    def _trim(self, entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
        trimmed = list(entries)
        while len(trimmed) > 1 and self._entry_tokens(trimmed) > self.token_budget:
            trimmed.pop(0)
        return trimmed

    @staticmethod
    def _entry_tokens(entries: list[dict[str, Any]]) -> int:
        return sum(
            rough_token_count(
                f"{entry.get('channel', '')} {entry.get('author', '')} {entry.get('content', '')}"
            )
            for entry in entries
        )
