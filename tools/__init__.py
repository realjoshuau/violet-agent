from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from . import browser, email_tool, network, terminal


ToolCallable = Callable[..., Awaitable[Any]]


@dataclass(frozen=True)
class ToolContext:
    requester_id: str
    owner_id: str
    guild_id: str
    channel_name: str

    @property
    def is_owner(self) -> bool:
        return str(self.requester_id) == str(self.owner_id)


TOOLS: dict[str, ToolCallable] = {
    "execute": terminal.execute,
    "screenshot": browser.screenshot,
    "send_email": email_tool.send,
    "http_get": network.get,
    "http_post": network.post,
}


OWNER_ONLY_TOOLS = {"execute"}


TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "execute",
            "description": "Run a shell command on the host. Owner only.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string"},
                    "timeout": {"type": "integer", "default": 30},
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "screenshot",
            "description": "Capture a PNG screenshot of a URL.",
            "parameters": {
                "type": "object",
                "properties": {"url": {"type": "string"}},
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "send_email",
            "description": "Send an email through the configured SMTP account.",
            "parameters": {
                "type": "object",
                "properties": {
                    "to": {"type": "string"},
                    "subject": {"type": "string"},
                    "body": {"type": "string"},
                    "bcc": {"type": "string"},
                },
                "required": ["to", "subject", "body"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "http_get",
            "description": "Fetch a URL with HTTP GET and return the response text.",
            "parameters": {
                "type": "object",
                "properties": {"url": {"type": "string"}},
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "http_post",
            "description": "Send a JSON HTTP POST request and return the response text.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string"},
                    "payload": {"type": "object"},
                },
                "required": ["url", "payload"],
            },
        },
    },
]
