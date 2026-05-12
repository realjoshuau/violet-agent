from __future__ import annotations

import tempfile
import unittest
import asyncio
import os
from typing import Any

import agent as agent_module
from agent import AgentRequest, VioletAgent
from config import Settings
from memory import MemoryStore
from personas import PeopleStore
from storage import Database
from config import load_settings


class FakeClient:
    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self.responses = responses
        self.calls: list[dict[str, Any]] = []

    async def chat(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        return self.responses.pop(0)


def settings_for(db_path: str) -> Settings:
    return Settings(
        log_level="INFO",
        discord_bot_token="",
        owner_discord_id=1,
        ollama_model="test-model",
        ollama_base_url="http://localhost:11434",
        context_token_budget=4096,
        max_tool_calls_per_turn=3,
        email_rate_limit=5,
        screenshot_rate_limit=10,
        db_path=db_path,
        people_path="personas/people.yaml",
        reply_to_rejected_dm=True,
        smtp_host="",
        smtp_port=587,
        smtp_username="",
        smtp_password="",
        smtp_from="",
        smtp_default_bcc="",
        smtp_use_tls=True,
        http_timeout_seconds=30,
    )


class AgentTest(unittest.TestCase):
    def make_agent(self, responses: list[dict[str, Any]]):
        tmp = tempfile.TemporaryDirectory()
        db = Database(":memory:")
        db.init()
        cfg = settings_for(":memory:")
        memory = MemoryStore(db, cfg.context_token_budget)
        violet = VioletAgent(
            memory=memory,
            people=PeopleStore(),
            db=db,
            config=cfg,
            llm_client=FakeClient(responses),
        )
        return tmp, db, memory, violet

    def run_async(self, coro):
        return asyncio.run(coro)

    def test_should_respond_on_name_without_classifier(self) -> None:
        tmp, db, _memory, violet = self.make_agent([])
        self.addCleanup(tmp.cleanup)
        self.addCleanup(db.close)

        self.assertTrue(
            self.run_async(violet.should_respond("violet help", "guild", False, False))
        )

    def test_relevance_classifier_can_trigger_response(self) -> None:
        tmp, db, _memory, violet = self.make_agent(
            [{"message": {"role": "assistant", "content": "yes"}}]
        )
        self.addCleanup(tmp.cleanup)
        self.addCleanup(db.close)

        self.assertTrue(
            self.run_async(violet.should_respond("can the bot do this?", "guild", False, False))
        )

    def test_denies_non_owner_terminal_and_logs(self) -> None:
        tmp, db, _memory, violet = self.make_agent([])
        self.addCleanup(tmp.cleanup)
        self.addCleanup(db.close)

        request = AgentRequest(
            content="run ls",
            author_name="NotOwner",
            author_id="2",
            context_key="guild",
            channel_name="general",
            guild_id="guild",
            server_name="Server",
        )
        text, attachment = self.run_async(
            violet._execute_tool("execute", {"command": "ls"}, request)
        )

        self.assertIsNone(attachment)
        self.assertIn("owner-only", text)
        row = db._conn.execute("SELECT status, error FROM tool_calls").fetchone()
        self.assertEqual(row["status"], "denied")
        self.assertEqual(row["error"], "Owner-only tool")

    def test_generate_executes_tool_then_final_response(self) -> None:
        async def fake_tool(url: str) -> str:
            return f"tool fetched {url}"

        original_tools = dict(agent_module.TOOLS)
        agent_module.TOOLS["http_get"] = fake_tool
        self.addCleanup(lambda: agent_module.TOOLS.update(original_tools))

        tmp, db, memory, violet = self.make_agent(
            [
                {"message": {"role": "assistant", "content": "technical"}},
                {
                    "message": {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            {
                                "function": {
                                    "name": "http_get",
                                    "arguments": {"url": "https://example.test"},
                                }
                            }
                        ],
                    }
                },
                {"message": {"role": "assistant", "content": "done"}},
            ]
        )
        self.addCleanup(tmp.cleanup)
        self.addCleanup(db.close)

        request = AgentRequest(
            content="fetch it",
            author_name="Owner",
            author_id="1",
            context_key="guild",
            channel_name="general",
            guild_id="guild",
            server_name="Server",
        )

        response = self.run_async(violet.generate(request))

        self.assertEqual(response.text, "done")
        self.assertEqual(memory.window("guild")[-1]["content"], "done")
        row = db._conn.execute("SELECT status, tool FROM tool_calls").fetchone()
        self.assertEqual(row["status"], "success")
        self.assertEqual(row["tool"], "http_get")

    def test_generate_logs_empty_model_response(self) -> None:
        tmp, db, _memory, violet = self.make_agent(
            [
                {"message": {"role": "assistant", "content": "casual"}},
                {"message": {"role": "assistant", "content": ""}},
            ]
        )
        self.addCleanup(tmp.cleanup)
        self.addCleanup(db.close)

        request = AgentRequest(
            content="hello",
            author_name="Owner",
            author_id="1",
            context_key="guild",
            channel_name="general",
            guild_id="guild",
            server_name="Server",
        )

        with self.assertLogs("violet", level="INFO") as captured:
            response = self.run_async(violet.generate(request))

        self.assertEqual(response.text, "")
        logs = "\n".join(captured.output)
        self.assertIn("model_response stage=generate", logs)
        self.assertIn("content_len=0", logs)

    def test_load_settings_reads_log_level(self) -> None:
        original = os.environ.get("LOG_LEVEL")
        os.environ["LOG_LEVEL"] = "DEBUG"
        try:
            settings = load_settings()
        finally:
            if original is None:
                os.environ.pop("LOG_LEVEL", None)
            else:
                os.environ["LOG_LEVEL"] = original

        self.assertEqual(settings.log_level, "DEBUG")
