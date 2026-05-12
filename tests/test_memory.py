from __future__ import annotations

import unittest

from memory import MemoryStore, context_key_for_dm, context_key_for_guild
from storage import Database


class MemoryStoreTest(unittest.TestCase):
    def test_append_trims_oldest_entries(self) -> None:
        db = Database(":memory:")
        db.init()
        memory = MemoryStore(db, token_budget=4)
        self.addCleanup(db.close)

        memory.append("1", "general", "A", "1", "x" * 40, persist=False)
        memory.append("1", "general", "B", "2", "ok", persist=False)

        window = memory.window("1")
        self.assertEqual(len(window), 1)
        self.assertEqual(window[0]["author"], "B")

    def test_hydrates_persisted_messages(self) -> None:
        db = Database(":memory:")
        db.init()
        self.addCleanup(db.close)
        memory = MemoryStore(db, token_budget=4096)
        memory.append("guild", "general", "Alex", "123", "hello")

        fresh = MemoryStore(db, token_budget=4096)
        self.assertEqual(fresh.window("guild")[0]["content"], "hello")

    def test_context_keys(self) -> None:
        self.assertEqual(context_key_for_guild(123), "123")
        self.assertEqual(context_key_for_dm(456), "dm_456")
