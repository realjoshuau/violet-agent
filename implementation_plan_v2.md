# Violet Implementation Plan v2

## Summary

Implement the `v2_plan.md` concurrency and stability fixes on top of the current agent code. Preserve the already-implemented behavior where Discord responses use `message.reply(..., mention_author=False)` and relevance classification (`should_respond` / `classify_relevance`) uses `qwen2.5:0.5b` with its current custom JSON prompt.

## Key Changes

- Add sequential guild processing in `main.py`:
  - Keep DM handling on the current direct path.
  - For guild messages, enqueue accepted non-bot messages into one `asyncio.Queue` per guild/context key.
  - Run one worker per guild so memory append, relevance classification, generation, reply send, and assistant memory append happen in strict order.
  - Keep the existing guild allowlist behavior unless intentionally changed later.
  - Keep sending responses with `message.reply(content=..., files=..., mention_author=False)`, not `channel.send`.

- Update `MemoryStore` for stable snapshots and explicit roles:
  - Store each memory entry with `role: "user"` or `role: "assistant"`.
  - Preserve backward compatibility for persisted rows that do not have a role by deriving assistant entries from `author_id == "assistant"`.
  - Add `get_snapshot(key)` returning a deep copy of the trimmed context.
  - Add `append_assistant(key, channel, content, ts=None, persist=True)` as the only path for recording Violet responses.
  - Add `get_recent_assistant(key, n=3)` for repetition detection.

- Refactor `VioletAgent` to generate from frozen context:
  - Build prompts from an explicit snapshot instead of calling `memory.window()` during generation.
  - Keep `_classify_relevance()` on `qwen2.5:0.5b` with the current custom JSON prompt and parsing.
  - Keep `_classify_depth()` on `qwen2.5:0.5b`; make it use the same frozen snapshot used for generation.
  - Do not append assistant responses inside `generate()`; return `AgentResponse` and let the guild worker append only after a successful Discord reply.

- Add response-loop protections:
  - In the system prompt, add: "Never repeat a response you have already sent. If you have nothing new to add, stay silent. Do not use your previous responses as the topic of the current response."
  - Before sending, compare the generated text against the last three assistant messages after simple normalization.
  - If repeated, retry generation once with an extra instruction to respond meaningfully to the newest message or output `[SKIP]`.
  - If final text is empty or `[SKIP]`, do not send or append an assistant message.

## Public Interfaces

- `MemoryStore.append(...)` gains optional `role="user"` while keeping current call sites valid.
- New memory APIs:
  - `MemoryStore.get_snapshot(key) -> list[dict]`
  - `MemoryStore.append_assistant(key, channel, content, ts=None, persist=True) -> None`
  - `MemoryStore.get_recent_assistant(key, n=3) -> list[str]`
- `VioletAgent.generate(...)` accepts a frozen context snapshot through `AgentRequest.context_snapshot`; generation, depth classification, and tool follow-up use that immutable view of the conversation.
- Relevance classification remains callable through `VioletAgent.should_respond(...)` and continues using `qwen2.5:0.5b`.

## Test Plan

- Memory tests:
  - Appended user messages include `role: "user"`.
  - `append_assistant()` stores `role: "assistant"` and persists with `author_id == "assistant"`.
  - Hydrated old persisted messages still derive assistant role correctly.
  - `get_snapshot()` returns a deep copy that does not change after later appends.
  - `get_recent_assistant()` returns only assistant content in order.

- Agent tests:
  - Relevance classifier still uses model `qwen2.5:0.5b`.
  - Depth classifier still uses model `qwen2.5:0.5b`.
  - Prompt building maps user snapshot entries to `role: "user"` and assistant entries to `role: "assistant"`.
  - `generate()` no longer mutates memory directly.
  - Repeated assistant output retries once and suppresses `[SKIP]`.

- Main flow tests:
  - Guild messages are processed sequentially per guild.
  - Messages from different guilds can process independently.
  - Non-response guild chatter is still appended to memory.
  - Responses are sent with `message.reply(..., mention_author=False)`.
  - Assistant memory append happens only after a response is actually sent.

## Assumptions

- No SQLite migration is required for `role`; persisted compatibility is handled in memory by deriving role from `author_id`.
- DM traffic remains low enough to keep the current direct handling path.
- The existing guild allowlist remains in place.
- The relevance classifier's current custom prompt and JSON parsing are retained unless a separate prompt-tuning task changes them.
