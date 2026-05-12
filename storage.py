from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from threading import RLock
from typing import Any


class Database:
    def __init__(self, path: str) -> None:
        self.path = path
        self._lock = RLock()
        parent = Path(path).parent
        if str(parent) != ".":
            parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def init(self) -> None:
        with self._lock:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id TEXT,
                    channel_name TEXT,
                    author TEXT,
                    author_id TEXT,
                    content TEXT,
                    ts INTEGER
                );

                CREATE TABLE IF NOT EXISTS rate_limits (
                    tool TEXT,
                    ts INTEGER
                );

                CREATE TABLE IF NOT EXISTS reminders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id TEXT,
                    channel_id TEXT,
                    message TEXT,
                    fire_at INTEGER
                );

                CREATE TABLE IF NOT EXISTS tool_calls (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    requester_id TEXT,
                    guild_id TEXT,
                    channel_name TEXT,
                    tool TEXT,
                    arguments TEXT,
                    status TEXT,
                    error TEXT,
                    ts INTEGER
                );
                """
            )
            self._conn.commit()

    def insert_message(
        self,
        guild_id: str,
        channel_name: str,
        author: str,
        author_id: str,
        content: str,
        ts: int | None = None,
    ) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO messages (guild_id, channel_name, author, author_id, content, ts)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (guild_id, channel_name, author, author_id, content, ts or int(time.time())),
            )
            self._conn.commit()

    def load_recent_messages(self, guild_id: str, limit: int = 200) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT channel_name, author, author_id, content, ts
                FROM messages
                WHERE guild_id = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (guild_id, limit),
            ).fetchall()
        return [
            {
                "channel": row["channel_name"],
                "author": row["author"],
                "author_id": row["author_id"],
                "content": row["content"],
                "ts": row["ts"],
            }
            for row in reversed(rows)
        ]

    def record_rate_limit_event(self, tool: str, ts: int | None = None) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO rate_limits (tool, ts) VALUES (?, ?)",
                (tool, ts or int(time.time())),
            )
            self._conn.commit()

    def count_rate_limit_events(self, tool: str, since_ts: int) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) AS count FROM rate_limits WHERE tool = ? AND ts >= ?",
                (tool, since_ts),
            ).fetchone()
        return int(row["count"])

    def check_rate_limit(self, tool: str, max_events: int, window_seconds: int = 3600) -> bool:
        return self.count_rate_limit_events(tool, int(time.time()) - window_seconds) < max_events

    def log_tool_call(
        self,
        requester_id: str,
        guild_id: str,
        channel_name: str,
        tool: str,
        arguments: dict[str, Any],
        status: str,
        error: str = "",
        ts: int | None = None,
    ) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO tool_calls
                    (requester_id, guild_id, channel_name, tool, arguments, status, error, ts)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    requester_id,
                    guild_id,
                    channel_name,
                    tool,
                    json.dumps(arguments, sort_keys=True, default=str)[:4000],
                    status,
                    error[:2000],
                    ts or int(time.time()),
                ),
            )
            self._conn.commit()
