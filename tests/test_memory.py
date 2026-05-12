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
        self.assertEqual(window[0]["role"], "user")

    def test_hydrates_persisted_messages(self) -> None:
        db = Database(":memory:")
        db.init()
        self.addCleanup(db.close)
        memory = MemoryStore(db, token_budget=4096)
        memory.append("guild", "general", "Alex", "123", "hello")

        fresh = MemoryStore(db, token_budget=4096)
        self.assertEqual(fresh.window("guild")[0]["content"], "hello")
        self.assertEqual(fresh.window("guild")[0]["role"], "user")

    def test_append_assistant_and_recent_assistant(self) -> None:
        db = Database(":memory:")
        db.init()
        self.addCleanup(db.close)
        memory = MemoryStore(db, token_budget=4096)

        memory.append("guild", "general", "Alex", "123", "hello", persist=False)
        memory.append_assistant("guild", "general", "hi", persist=False)
        memory.append_assistant("guild", "general", "there", persist=False)

        window = memory.window("guild")
        self.assertEqual(window[1]["author_id"], "assistant")
        self.assertEqual(window[1]["role"], "assistant")
        self.assertEqual(memory.get_recent_assistant("guild", 2), ["hi", "there"])

    def test_snapshot_is_deep_copy(self) -> None:
        db = Database(":memory:")
        db.init()
        self.addCleanup(db.close)
        memory = MemoryStore(db, token_budget=4096)

        memory.append("guild", "general", "Alex", "123", "hello", persist=False)
        snapshot = memory.get_snapshot("guild")
        snapshot[0]["content"] = "changed"
        memory.append("guild", "general", "Blair", "456", "new", persist=False)

        self.assertEqual(memory.window("guild")[0]["content"], "hello")
        self.assertEqual(len(snapshot), 1)

    def test_hydrated_assistant_role_is_derived_for_old_rows(self) -> None:
        db = Database(":memory:")
        db.init()
        self.addCleanup(db.close)
        db.insert_message("guild", "general", "Violet", "assistant", "old reply")

        memory = MemoryStore(db, token_budget=4096)

        self.assertEqual(memory.window("guild")[0]["role"], "assistant")

    def test_context_keys(self) -> None:
        self.assertEqual(context_key_for_guild(123), "123")
        self.assertEqual(context_key_for_dm(456), "dm_456")
