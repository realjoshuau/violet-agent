from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import yaml

from scripts.generate_people_yaml import (
    DiscordMessage,
    PersonSeed,
    build_profile_prompt,
    extract_messages,
    load_cached_messages,
    parse_user_ids,
    render_people_yaml,
    save_cached_messages,
)


class GeneratePeopleYamlTest(unittest.TestCase):
    def test_parse_user_ids_accepts_repeated_and_comma_separated_values(self) -> None:
        self.assertEqual(
            parse_user_ids(["123,456", "456", "789"]),
            ["123", "456", "789"],
        )

    def test_parse_user_ids_rejects_non_numeric_values(self) -> None:
        with self.assertRaises(ValueError):
            parse_user_ids(["123", "not-an-id"])

    def test_render_people_yaml_matches_people_schema(self) -> None:
        rendered = render_people_yaml(
            [
                PersonSeed(
                    discord_id="123",
                    name="Alex",
                    notes="Discord username: alex\nTODO: add notes.",
                )
            ]
        )

        parsed = yaml.safe_load(rendered)
        self.assertEqual(parsed["people"][0]["discord_id"], "123")
        self.assertEqual(parsed["people"][0]["name"], "Alex")
        self.assertIn("TODO", parsed["people"][0]["notes"])

    def test_extract_messages_flattens_discord_search_groups(self) -> None:
        payload = {
            "messages": [
                [
                    {
                        "content": "hello",
                        "timestamp": "2026-05-12T00:00:00.000000+00:00",
                        "channel_id": "999",
                        "author": {
                            "id": "123",
                            "username": "alex",
                            "global_name": "Alex",
                        },
                    },
                    {
                        "content": "other",
                        "timestamp": "2026-05-12T00:01:00.000000+00:00",
                        "channel_id": "999",
                        "author": {"id": "456", "username": "sam"},
                    },
                ]
            ]
        }

        messages = extract_messages(payload, "123")

        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0].content, "hello")
        self.assertEqual(messages[0].author_name, "Alex")

    def test_build_profile_prompt_instructs_against_sensitive_inferences(self) -> None:
        prompt = build_profile_prompt(
            "123",
            [
                DiscordMessage(
                    content="keep it short lol",
                    timestamp="2026-05-12T00:00:00.000000+00:00",
                    channel_id="999",
                    author_id="123",
                    author_name="Alex",
                )
            ],
        )

        self.assertIn("communication preferences", prompt)
        self.assertIn("Do not infer protected traits", prompt)
        self.assertIn("keep it short lol", prompt)

    def test_message_cache_round_trips_messages(self) -> None:
        with TemporaryDirectory() as tmp:
            cache_dir = Path(tmp)
            messages = [
                DiscordMessage(
                    content="cached hello",
                    timestamp="2026-05-12T00:00:00.000000+00:00",
                    channel_id="999",
                    author_id="123",
                    author_name="Alex",
                )
            ]

            save_cached_messages(cache_dir, 111, 999, "123", messages, complete=True)
            cached = load_cached_messages(cache_dir, 111, 999, "123", sample_limit=75)

        self.assertIsNotNone(cached)
        assert cached is not None
        self.assertTrue(cached.complete)
        self.assertEqual(cached.messages, messages)

    def test_incomplete_message_cache_must_satisfy_sample_limit(self) -> None:
        with TemporaryDirectory() as tmp:
            cache_dir = Path(tmp)
            messages = [
                DiscordMessage(
                    content="one",
                    timestamp="2026-05-12T00:00:00.000000+00:00",
                    channel_id="999",
                    author_id="123",
                    author_name="Alex",
                )
            ]

            save_cached_messages(cache_dir, 111, 999, "123", messages, complete=False)

            self.assertIsNone(
                load_cached_messages(cache_dir, 111, 999, "123", sample_limit=2)
            )
            cached = load_cached_messages(cache_dir, 111, 999, "123", sample_limit=1)

        self.assertIsNotNone(cached)
        assert cached is not None
        self.assertEqual(cached.messages, messages)
