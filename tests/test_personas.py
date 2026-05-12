from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from personas import PeopleStore


class PeopleStoreTest(unittest.TestCase):
    def test_loads_notes_by_discord_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "people.yaml"
            path.write_text(
                """
people:
  - discord_id: "123"
    name: "Alex"
    notes: |
      Likes short answers.
""",
                encoding="utf-8",
            )

            store = PeopleStore.load(str(path))
            self.assertIn("Alex", store.notes_for("123"))
            self.assertIn("Likes short answers.", store.notes_for("123"))
            self.assertEqual(store.notes_for("missing"), "")
