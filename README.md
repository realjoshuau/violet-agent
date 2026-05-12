# Violet

> [!NOTE]
> This project is both in early development, a personal exercise, AND mostly AI-agent programmed. There are obvious risks in running code from an AI agent! The production deployment of this project is on a isolated machine with NO access to sensitive data.


Violet is a Discord AI agent backed by a local Ollama model. It keeps per-server context, can use tools, restricts terminal execution to the owner, and logs every tool attempt to SQLite.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
cp .env.example .env
```

Fill in `.env`, especially:

- `DISCORD_BOT_TOKEN`
- `OWNER_DISCORD_ID`
- `OLLAMA_MODEL`
- SMTP values if email sending should work

Then run:

```bash
python3 main.py
```

## Tests

```bash
python3 -m unittest discover -v
```

The unit tests mock external services and do not require Discord, Ollama, SMTP, or Playwright.

## People YAML

Generate a private `personas/people.yaml` from Discord message history:

```bash
python3 scripts/generate_people_yaml.py \
  --guild-id 123456789012345678 \
  --channel-id 234567890123456789 \
  --user-id 345678901234567890 \
  --user-id 456789012345678901
```

The script uses `DISCORD_BOT_TOKEN` from `.env`, searches that channel for each user's messages through Discord's guild message search API, sends the samples to Ollama, and writes interaction notes about tone, communication preferences, interests, and how Violet should respond to them. It refuses to overwrite an existing output file unless `--force` is passed.

Downloaded Discord message samples are cached in `.cache/generate_people_yaml/messages` by default, so changing the prompt or Ollama model does not require downloading the same messages again. Use `--refresh-message-cache` to force a fresh Discord search or `--no-message-cache` to disable the cache.

Useful options:

```bash
python3 scripts/generate_people_yaml.py \
  --guild-id 123456789012345678 \
  --channel-id 234567890123456789 \
  --user-id 345678901234567890,456789012345678901 \
  --sample-limit 100 \
  --refresh-message-cache \
  --force
```
