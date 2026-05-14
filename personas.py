from __future__ import annotations

from pathlib import Path

import yaml


class PeopleStore:
    def __init__(self, people: dict[str, dict[str, str]] | None = None) -> None:
        self.people = people or {}
        self.raw = {"people": [{"discord_id": discord_id, **info} for discord_id, info in self.people.items()]}

    @classmethod
    def load(cls, path: str) -> "PeopleStore":
        people_path = Path(path)
        if not people_path.exists():
            return cls()
        raw = yaml.safe_load(people_path.read_text(encoding="utf-8")) or {}
        entries = raw.get("people", [])
        people: dict[str, dict[str, str]] = {}
        for entry in entries:
            discord_id = str(entry.get("discord_id", "")).strip()
            if not discord_id:
                continue
            people[discord_id] = {
                "name": str(entry.get("name", "")).strip(),
                "notes": str(entry.get("notes", "")).strip(),
                "voice": str(entry.get("voice", "")).strip(),
            }
        return cls(people)

    def notes_for(self, discord_id: int | str) -> str:
        person = self.people.get(str(discord_id))
        if not person:
            return ""
        name = person.get("name", "")
        notes = person.get("notes", "")
        if name and notes:
            return f"{name}: {notes}"
        return notes or name

    def voice_for(self, discord_id: int | str) -> str:
        person = self.people.get(str(discord_id))
        if not person:
            return ""
        return str(person.get("voice") or "").strip()
