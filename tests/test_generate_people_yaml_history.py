from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import yaml

from scripts.generate_people_yaml_history import (
    DiscordMessage,
    PersonSeed,
    build_interaction_prompt,
    load_cached_channel_history,
    render_people_yaml,
    save_channel_manifest,
    save_channel_page,
)


class GeneratePeopleYamlHistoryTest(unittest.TestCase):
    def test_render_people_yaml_includes_voice(self) -> None:
        rendered = render_people_yaml(
            [
                PersonSeed(
                    discord_id="123",
                    name="Alex",
                    notes="Notes.",
                    voice="Answer as Discord ID 123.",
                )
            ]
        )
        parsed = yaml.safe_load(rendered)
        self.assertEqual(parsed["people"][0]["discord_id"], "123")
        self.assertIn("voice", parsed["people"][0])

    def test_interaction_prompt_mentions_ids(self) -> None:
        prompt = build_interaction_prompt(
            "111",
            "222",
            [
                DiscordMessage(
                    content="hey",
                    timestamp="2026-05-12T00:00:00+00:00",
                    channel_id="1",
                    author_id="111",
                    author_name="A",
                )
            ],
        )
        self.assertIn("Discord user A ID: 111", prompt)
        self.assertIn("Discord user B ID: 222", prompt)
        self.assertIn("111: hey", prompt)

    def test_history_cache_round_trip(self) -> None:
        with TemporaryDirectory() as tmp:
            cache_dir = Path(tmp)
            messages = [
                DiscordMessage(
                    content="cached",
                    timestamp="2026-05-12T00:00:00+00:00",
                    channel_id="1",
                    author_id="123",
                    author_name="Alex",
                )
            ]
            save_channel_page(
                cache_dir=cache_dir,
                guild_id=1,
                channel_id=1,
                page_index=0,
                before_id="",
                next_before="1",
                messages=messages,
            )
            save_channel_manifest(
                cache_dir=cache_dir,
                guild_id=1,
                channel_id=1,
                complete=True,
                total_messages=1,
                max_messages=0,
                last_page_index=0,
                next_before="",
            )
            cached = load_cached_channel_history(
                cache_dir=cache_dir,
                guild_id=1,
                channel_id=1,
                max_messages=0,
            )
            self.assertIsNotNone(cached)
            assert cached is not None
            self.assertTrue(cached.manifest.complete)
            self.assertEqual(cached.messages, messages)
