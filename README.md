# Violet

> [!NOTE]
> This project is both in early development, a personal exercise, AND mostly AI-agent programmed. There are obvious risks in running code from an AI agent! The production deployment of this project is on a isolated machine with NO access to sensitive data.

>[!CAUTION]
> This project provides no guarantees of even working. This is a vibe-coded personal project for _fun_, and the code quality is significantly worse than a typical human project (and is not reflective of other projects). 

>[!TIP]
> This is not the latest version of the agent. The agent has extra features that are not merged upstream into this repository yet (mostly Discord-integration tools and some classification upgrades regarding emojis).

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
