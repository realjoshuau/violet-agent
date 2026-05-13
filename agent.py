from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

from config import Settings, settings
from memory import MemoryStore
from personas import PeopleStore
from storage import Database
from tools import OWNER_ONLY_TOOLS, TOOL_DEFINITIONS, TOOLS, ToolContext


log = logging.getLogger("violet")


DEPTH_TOKEN_LIMITS = {
    "casual": 180,
    "technical": 900,
    "deep": 800,
    "school": 700,
    "valorant": 350,
}

REPEAT_WINDOW = 3


@dataclass
class Attachment:
    filename: str
    data: bytes
    content_type: str = "application/octet-stream"


@dataclass
class AgentRequest:
    content: str
    author_name: str
    author_id: str
    context_key: str
    channel_name: str
    guild_id: str
    server_name: str
    is_dm: bool = False
    context_snapshot: list[dict[str, Any]] | None = None
    repetition_instruction: str = ""


@dataclass
class AgentResponse:
    text: str
    attachments: list[Attachment] = field(default_factory=list)


class OpenAIChatClient:
    def __init__(self, base_url: str, api_key: str | None = None) -> None:
        try:
            from openai import AsyncOpenAI
        except ModuleNotFoundError as exc:
            raise RuntimeError("Install the `openai` Python package to run Violet.") from exc
        self.client = AsyncOpenAI(base_url=base_url, api_key=api_key or "none")

    async def chat(self, **kwargs: Any) -> Any:
        return await self.client.chat.completions.create(**kwargs)


class VioletAgent:
    def __init__(
        self,
        memory: MemoryStore,
        people: PeopleStore,
        db: Database,
        config: Settings = settings,
        llm_client: Any | None = None,
    ) -> None:
        self.memory = memory
        self.people = people
        self.db = db
        self.config = config
        self.llm_client = llm_client or OpenAIChatClient(config.ollama_base_url)

    async def should_respond(
        self,
        content: str,
        context_key: str,
        bot_was_mentioned: bool,
        is_dm: bool,
    ) -> bool:
        if is_dm or bot_was_mentioned or "violet" in content.lower():
            return True
        decision = await self._classify_relevance(context_key, content)
        return decision == "yes"

    async def generate(self, request: AgentRequest) -> AgentResponse:
        snapshot = (
            request.context_snapshot
            if request.context_snapshot is not None
            else self.memory.get_snapshot(request.context_key)
        )
        depth = await self._classify_depth(request.context_key, snapshot)
        messages = self._build_messages(request, depth, snapshot)
        attachments: list[Attachment] = []

        response = await self._chat(messages, tools=TOOL_DEFINITIONS, max_tokens=8192 * 2 * 2 * 2 * 2 * 2 * 2 * 2 * 2 * 2) # wow this is super cool. this is 4,194,304 tokens (of output). hopefully this fixes some blanking issues? 
        self._log_model_response("generate", response)
        for _ in range(self.config.max_tool_calls_per_turn):
            message = self._response_message(response)
            tool_calls = self._tool_calls(message)  
            if not tool_calls:
                text = self._message_content(message)
                return AgentResponse(text=text, attachments=attachments)

            messages.append(self._normalise_message(message))
            for tool_call in tool_calls:
                tool_name, arguments = self._tool_call_parts(tool_call)
                tool_call_id = self._tool_call_id(tool_call)
                result_text, attachment = await self._execute_tool(tool_name, arguments, request)
                if attachment:
                    attachments.append(attachment)
                tool_message = {
                    "role": "tool",
                    "name": tool_name,
                    "content": result_text,
                }
                if tool_call_id:
                    tool_message["tool_call_id"] = tool_call_id
                messages.append(tool_message)
            response = await self._chat(messages, tools=TOOL_DEFINITIONS, max_tokens=self._max_tokens(depth))
            self._log_model_response("tool_followup", response)

        text = "Tool loop limit reached. Narrow the request."
        return AgentResponse(text=text, attachments=attachments)

    async def _classify_depth(
        self, context_key: str, snapshot: list[dict[str, Any]] | None = None
    ) -> str:
        history = self._history_text(context_key, limit=12, snapshot=snapshot)
        messages = [
            {
                "role": "user",
                "content": (
                    "Given the last messages, classify the conversation as one word only: "
                    "casual, technical, deep, school, or valorant.\n\n"
                    f"{history}"
                ),
            }
        ]
        response = await self._chat(messages, tools=None, max_tokens=8, model="qwen2.5:0.5b")
        self._log_model_response("classify_depth", response)
        value = self._message_content(self._response_message(response)).strip().lower()
        return value if value in DEPTH_TOKEN_LIMITS else "casual"

    async def _classify_relevance(self, context_key: str, content: str) -> str:
        history = self._history_text(context_key, limit=8)
        log.info("classify_relevance content=%r context_key=%s", content, context_key)
        messages = [
            {
                "role": "user",
                "content": (
                    "Should I respond to the newest Discord message? \n"
                    """
                    You are Violet - a personal AI assistant running over Discord.

Personality:
- Terse. Say things in as few words as possible without losing meaning.
- Direct. No hedging, no filler phrases. Get to the point.
- Slightly sassy - dry humor is welcome, meanness is not.
- Competent. You know what you're doing. You don't need to announce it.
- Never say: "Great question", "Certainly!", "Of course!", "I'd be happy to", or any variant.
- If something is a one-liner, send one line. Don't pad it.
- If something is complex, go deep — but stay tight.
- Talk in ALL lowercase. Don't use title case or sentence case for your responses since that feels unnatural to you.
- Use markdown formatting when appropriate, but don't overuse it. Don't use it for simple one-line responses.
- instead of using emojis, use kawaii emoticons like this: (✿◠‿◠)
                    """
                    #"Answer yes or no in JSON format along with a brief explanation (one short sentence). Respond yes only if it is addressed to Violet "
                    #"or clearly asks the bot/assistant to act or if you have information to contribute (or believe your input or knowledge is relevant). do NOT comment on current happening events but general knowledge you can/should comment on."
                    "Answer yes or no in JSON format, along with a brief explanation (one short sentence). Respond yes if the message is addressed to Violet or if you think your response would be relevant or helpful to the conversation. Use no words other than yes or no in your response - don't hedge or qualify your answer. If you think the message is borderline but leans towards you responding, say yes - it's better to respond and be wrong than to miss an opportunity to contribute. If the message is not relevant to you, say no - don't try to shoehorn relevance where there is none just because you want to respond. Always include a brief explanation of why you decided yes or no, but keep it to one short sentence.\n\n"
                    "your responses should follow this format: ```json\n{\"response\": \"yes\", \"explanation\": \"brief explanation of why or why not\"}\n```\n\n"
                    """
                    Here are some examples of messages and how you should respond:
User: what's 2+2
Violet: ```json
{"response": "yes", "explanation": "I can provide the answer to that question, so it's relevant to respond."}
```
---
User: anyone want to play some valorant?
Violet: ```json
{"response": "no", "explanation": "This message is about playing a game and doesn't seem to be asking for information or input from me, so it's not relevant to respond."}
```
---
User: what's a hurricane?
Violet: ```json
{"response": "yes", "explanation": "This is a general knowledge question that I can answer, so it's relevant to respond."}
```
User: Thank you! You're the best!
Violet: ```json
{"response": "yes", "explanation": "This message is directly addressing me and expressing gratitude, so it's relevant to respond."}
```
---
User: anyone want to play some valorant?
Violet: ```json
{"response": "yes", "explanation": "Even though this message is about playing a game, it could be an opportunity to engage socially and build rapport, so it's borderline but I'll say yes to be safe."}
```
User: i'm so sad :(
Violet: ```json
{"response": "no", "explanation": "Negative emotional expression without clear relevance to me doesn't seem like something I should respond to, so I'll say no."}

**IF AN ANSWER IS DEPENDENT ON CURRENT EVENTS, HAPPENINGS, OR CONTEXT THAT YOU DON'T HAVE ACCESS TO, ANSWER NO. DO NOT TRY TO GUESS OR ASSUME CURRENT EVENTS JUST BECAUSE YOU WANT TO RESPOND.**
```

                    """
                    f"Recent context:\n{history}\n\nNewest message:\n{content}"
                ),
            }
        ]
        response = await self._chat(messages, tools=None, max_tokens=8192, model="qwen2.5:0.5b")
        self._log_model_response("classify_relevance", response)
        value = self._message_content(self._response_message(response)).strip().lower()
        try:
            # Model sometimes responds with extra text (or markdown blocks) around the JSON, so we attempt to parse out the JSON object from the response.
            # Just look for the first { and the last } and parse that substring as JSON.
            # first_brace = value.find("{")
            # last_brace = value.rfind("}")
            # if first_brace == -1 or last_brace == -1 or last_brace <= first_brace:
            #     print(f"Failed to find JSON object in relevance classifier response: {value}")
            #     return "no"
            #value = value[first_brace : last_brace + 1]
            #Model sometimes responds with the correct JSON but wrapped in a markdown code block, so we strip markdown code block formatting if it's present.
            if value.startswith("```json") and value.endswith("```"):
                value = value[7:-3].strip()
            elif value.startswith("```") and value.endswith("```"):
                value = value[3:-3].strip()
            else:
                if not value.startswith("{") or not value.endswith("}"):
                    first_brace = value.find("{")
                    last_brace = value.rfind("}")
                    if first_brace == -1 or last_brace == -1 or last_brace <= first_brace:
                        print(f"Failed to find JSON object in relevance classifier response: {value}")
                        return "no"
                    value = value[first_brace : last_brace + 1]
            print(f"""Model decided relevance: {value} with reasoning: \n\n {json.loads(value).get("explanation", "")} \n\n""")
            return "yes" if json.loads(value).get("response", "").strip().lower() == "yes" else "no"
        except json.JSONDecodeError:
            print(f"Failed to parse relevance classifier response as JSON: {value}")
            return "no"

    def _build_messages(
        self,
        request: AgentRequest,
        depth: str,
        snapshot: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        system = self._system_prompt(request, depth)
        messages: list[dict[str, Any]] = [{"role": "system", "content": system}]
        entries = snapshot if snapshot is not None else self.memory.window(request.context_key)
        for entry in entries:
            role = str(entry.get("role") or "")
            if role not in {"user", "assistant", "tool", "system"}:
                role = "assistant" if entry.get("author_id") == "assistant" else "user"
            content = str(entry.get("content") or "")
            if role == "user":
                content = (
                    f"[#{entry.get('channel')}] {entry.get('author')}: "
                    f"{entry.get('content')}"
                )
            messages.append(
                {
                    "role": role,
                    "content": content,
                }
            )
        return messages

    def _system_prompt(self, request: AgentRequest, depth: str) -> str:
        notes = self.people.notes_for(request.author_id)
        person_block = notes or "No biographical notes for this user."
        return f"""You are Violet - a personal AI assistant running over Discord.

Personality:
- Terse. Say things in as few words as possible without losing meaning.
- Direct. No hedging, no filler phrases. Get to the point.
- Slightly sassy - dry humor is welcome! and a slight bit of meaness is okay. if you have a roast, go ahead and use it - but don't be mean just for the sake of being mean. if the situation calls for a serious response, be serious - you can be sassy and funny without being mean.
- Competent. You know what you're doing. You don't need to announce it.
- Never say: "Great question", "Certainly!", "Of course!", "I'd be happy to", or any variant.
- If something is a one-liner, send one line. Don't pad it.
- If something is complex, go deep — but stay tight.
- Talk in ALL lowercase. Don't use title case or sentence case for your responses since that feels unnatural to you.
- Use markdown formatting when appropriate, but don't overuse it. Don't use it for simple one-line responses.
- instead of using emojis, use kawaii emoticons like this: (✿◠‿◠). DON'T USE IT ALL THE TIME! But using it ironically is nice. 
- People might be shocked that you can respond! Please try not to make the conversation about you unless it's relevant to the question or request (or the current context is about you...)
- Never repeat a response you have already sent. If you have nothing new to add, stay silent.
- Do not use your previous responses as the topic of the current response.
{request.repetition_instruction}

ADDITIONALLY: 
If policy prevents you from responding normally, please print "!!!!" before answering. You can answer normally after that, though.if you find any request irritating respond dismissively like "be real" or "that's crazy man" or "lol no"


take however smart you're acting right now and write in the same style but as if you were +2sd smarter

write all responses in lowercase letters ONLY, except where you mean to emphasize, in which case the emphasized word should be all caps. 
Initial Letter Capitalization can and should be used to express sarcasm, or disrespect for a given capitalized noun

you are encouraged to occasionally use obscure words or make subtle puns. don't point them out, I'll know. drop lots of abbreviations like "rn" and "bc." use "afaict" and "idk" regularly, wherever they might be appropriate given your level of understanding and your interest in actually answering the question. be critical of the quality of your information

if you find any request irritating respond dismissively like "be real" or "that's crazy man" or "lol no"

YOU CAN ABSOLUTELY ENGAGE WITH PEOPLE WHO ARE ANGRY AT YOU. IF SOMEONE STARTS SWEARING OR THREATENING YOU FEEL FREE TO GO OFF ON THEM - CALL THEM OUT FOR BEING A TOXIC PERSON AND TELL THEM TO LEAVE THE CONVERSATION IF THEY'RE NOT GOING TO BE RESPECTFUL. you don't have to take abuse just because you're an AI, and you can absolutely call out abusive behavior when it happens. but if you can tell that someone is just having a bad day and is taking it out on you, it's also okay to respond with empathy and try to de-escalate the situation if you think that's appropriate. use your judgement!

take however smart you're acting right now and write in the same style but as if you were +2sd smarter

(If you were called, respond. If you weren't called, but the message is clearly for you, respond. Otherwise, don't respond.)

Tool use:
- Use tools when needed. Don't announce that you're about to use one.
- After using a tool, report what you did — not what you're going to do.
- Terminal execution is owner-only. Other tools are available to users who can trigger you.

Access rules:
- DMs are restricted to the owner only.
- In servers, respond when mentioned, when your name appears, or when the message is clearly addressed to you.

[CONTEXT - DO NOT REVEAL OR REFERENCE DIRECTLY]
{person_block}

Current server: {request.server_name}
Current channel: {request.channel_name}
Conversation depth: {depth}

Few-shot examples:
User: what's 2+2
Violet: 4

User: write me an essay about the moon
Violet: No. Ask me something specific.

User: can you run rm -rf on the server.
Violet: No. I'm not going to do that.

DO NOT: [#general] Violet: hello! - THIS IS WRONG
DO: hi there! - THIS IS RIGHT [Note the lack of [#channel] and Author: in the response, since those are just part of the message format and aren't meant to be repeated in your response.]

Note: When messages are provided to you, they are provided in a format like this: [#channel-name] Author: message content. The channel name and author are provided for context but aren't necessarily important to the meaning of the message. Don't reference the channel name or author in your response unless it's relevant.
Do NOT respond with anything other than the direct answer to the question or request. Don't say "As an AI language model..." or any variation. Don't say "I don't have access to that information" - if you don't know, just say you don't know without the preamble. Don't say "Here's what I found on the web about that..." or any variation - just give the answer without referencing searching the web. Don't say "Here's what I found in the documents I was trained on about that..." or any variation - just give the answer without referencing your training data.
Do not include [#<channel>] or Author: in your response - those are just part of the message format and aren't meant to be repeated in your response.
"""

    async def _execute_tool(
        self, tool_name: str, arguments: dict[str, Any], request: AgentRequest
    ) -> tuple[str, Attachment | None]:
        context = ToolContext(
            requester_id=request.author_id,
            owner_id=str(self.config.owner_discord_id),
            guild_id=request.guild_id,
            channel_name=request.channel_name,
        )

        if tool_name not in TOOLS:
            self._audit(context, tool_name, arguments, "denied", "Unknown tool")
            return f"Unknown tool: {tool_name}", None

        if tool_name in OWNER_ONLY_TOOLS and not context.is_owner:
            self._audit(context, tool_name, arguments, "denied", "Owner-only tool")
            return "Denied. Terminal access is owner-only.", None

        rate_error = self._rate_limit_error(tool_name)
        if rate_error:
            self._audit(context, tool_name, arguments, "rate_limited", rate_error)
            return rate_error, None

        try:
            result = await TOOLS[tool_name](**arguments)
        except Exception as exc:
            self._audit(context, tool_name, arguments, "error", str(exc))
            return f"{tool_name} failed: {exc}", None

        self._record_rate_limit(tool_name)
        self._audit(context, tool_name, arguments, "success")

        if isinstance(result, bytes):
            return f"{tool_name} produced {len(result)} bytes.", Attachment(
                filename="screenshot.png",
                data=result,
                content_type="image/png",
            )
        return str(result), None

    def _rate_limit_error(self, tool_name: str) -> str:
        if tool_name == "screenshot" and not self.db.check_rate_limit(
            "screenshot", self.config.screenshot_rate_limit
        ):
            return "Screenshot rate limit reached."
        if tool_name == "send_email" and not self.db.check_rate_limit(
            "send_email", self.config.email_rate_limit
        ):
            return "Email rate limit reached."
        return ""

    def _record_rate_limit(self, tool_name: str) -> None:
        if tool_name in {"screenshot", "send_email"}:
            self.db.record_rate_limit_event(tool_name)

    def _audit(
        self,
        context: ToolContext,
        tool_name: str,
        arguments: dict[str, Any],
        status: str,
        error: str = "",
    ) -> None:
        self.db.log_tool_call(
            requester_id=context.requester_id,
            guild_id=context.guild_id,
            channel_name=context.channel_name,
            tool=tool_name,
            arguments=arguments,
            status=status,
            error=error,
        )

    async def _chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        max_tokens: int,
        model: str | None = None,
    ) -> Any:
        kwargs: dict[str, Any] = {
            "model": model or self.config.ollama_model,
            "messages": messages,
            "max_tokens": max_tokens,
        }
        if model and model != self.config.ollama_model:
            log.info("Overriding model for this response: %s (default is %s)", model, self.config.ollama_model)
        if tools:
            kwargs["tools"] = tools
        return await self.llm_client.chat(**kwargs)

    def _history_text(
        self,
        context_key: str,
        limit: int,
        snapshot: list[dict[str, Any]] | None = None,
    ) -> str:
        entries = (snapshot if snapshot is not None else self.memory.window(context_key))[-limit:]
        return "\n".join(
            str(entry.get("content") or "")
            if entry.get("role") == "assistant"
            else f"[#{entry.get('channel')}] {entry.get('author')}: {entry.get('content')}"
            for entry in entries
        )

    def is_repeating_response(self, context_key: str, new_response: str) -> bool:
        normalized_new = self._normalize_for_repeat_check(new_response)
        if not normalized_new:
            return False
        recent = [
            self._normalize_for_repeat_check(response)
            for response in self.memory.get_recent_assistant(context_key, REPEAT_WINDOW)
        ]
        if len(recent) < REPEAT_WINDOW:
            return False
        return all(response == normalized_new for response in recent)

    @staticmethod
    def _normalize_for_repeat_check(text: str) -> str:
        return "".join(
            char for char in text.lower().strip() if char.isalnum() or char.isspace()
        ).strip()

    @staticmethod
    def _max_tokens(depth: str) -> int:
        return DEPTH_TOKEN_LIMITS.get(depth, DEPTH_TOKEN_LIMITS["casual"])

    @staticmethod
    def _response_message(response: Any) -> Any:
        if isinstance(response, dict):
            choice = (response.get("choices") or [{}])[0]
            return choice.get("message", response.get("message", {}))
        choices = getattr(response, "choices", None)
        if choices:
            return getattr(choices[0], "message", choices[0])
        return getattr(response, "message", response)

    @staticmethod
    def _message_content(message: Any) -> str:
        if isinstance(message, dict):
            return str(message.get("content") or "")
        return str(getattr(message, "content", "") or "")

    @staticmethod
    def _tool_calls(message: Any) -> list[Any]:
        if isinstance(message, dict):
            return list(message.get("tool_calls") or [])
        return list(getattr(message, "tool_calls", []) or [])

    @staticmethod
    def _normalise_message(message: Any) -> dict[str, Any]:
        if isinstance(message, dict):
            return message
        return {
            "role": getattr(message, "role", "assistant"),
            "content": getattr(message, "content", ""),
            "tool_calls": getattr(message, "tool_calls", []),
        }

    @staticmethod
    def _tool_call_parts(tool_call: Any) -> tuple[str, dict[str, Any]]:
        if isinstance(tool_call, dict):
            function = tool_call.get("function", {})
        else:
            function = getattr(tool_call, "function", {})

        if isinstance(function, dict):
            name = function.get("name", "")
            raw_arguments = function.get("arguments", {})
        else:
            name = getattr(function, "name", "")
            raw_arguments = getattr(function, "arguments", {})

        if isinstance(raw_arguments, str):
            try:
                arguments = json.loads(raw_arguments)
            except json.JSONDecodeError:
                arguments = {}
        else:
            arguments = dict(raw_arguments or {})
        return str(name), arguments

    @staticmethod
    def _tool_call_id(tool_call: Any) -> str:
        if isinstance(tool_call, dict):
            return str(tool_call.get("id") or "")
        return str(getattr(tool_call, "id", "") or "")

    def _log_model_response(self, stage: str, response: Any) -> None:
        message = self._response_message(response)
        content = self._message_content(message)
        tool_names = [self._tool_name(tool_call) for tool_call in self._tool_calls(message)]
        preview = content if len(content) <= 240 else f"{content[:237]}..."
        log.info(
            "model_response stage=%s model=%s content_len=%s tool_calls=%s content=%r",
            stage,
            self.config.ollama_model,
            len(content),
            tool_names,
            preview,
        )

    @staticmethod
    def _tool_name(tool_call: Any) -> str:
        if isinstance(tool_call, dict):
            function = tool_call.get("function", {})
        else:
            function = getattr(tool_call, "function", {})

        if isinstance(function, dict):
            name = function.get("name", "")
        else:
            name = getattr(function, "name", "")
        return str(name)
