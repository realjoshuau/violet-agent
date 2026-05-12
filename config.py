from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _load_dotenv(path: str = ".env") -> None:
    try:
        from dotenv import load_dotenv
    except ModuleNotFoundError:
        env_path = Path(path)
        if not env_path.exists():
            return
        for line in env_path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key, value = stripped.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            os.environ.setdefault(key, value)
    else:
        load_dotenv(path)


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    return int(raw)


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    return raw.lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    log_level: str
    discord_bot_token: str
    owner_discord_id: int
    ollama_model: str
    ollama_base_url: str
    context_token_budget: int
    max_tool_calls_per_turn: int
    email_rate_limit: int
    screenshot_rate_limit: int
    db_path: str
    people_path: str
    reply_to_rejected_dm: bool
    smtp_host: str
    smtp_port: int
    smtp_username: str
    smtp_password: str
    smtp_from: str
    smtp_default_bcc: str
    smtp_use_tls: bool
    http_timeout_seconds: int


def load_settings(env_file: str = ".env") -> Settings:
    _load_dotenv(env_file)
    return Settings(
        log_level=os.getenv("LOG_LEVEL", "INFO"),
        discord_bot_token=os.getenv("DISCORD_BOT_TOKEN", ""),
        owner_discord_id=_env_int("OWNER_DISCORD_ID", 0),
        ollama_model=os.getenv("OLLAMA_MODEL", "qwen3:latest"),
        ollama_base_url=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"),
        context_token_budget=_env_int("CONTEXT_TOKEN_BUDGET", 4096),
        max_tool_calls_per_turn=_env_int("MAX_TOOL_CALLS_PER_TURN", 5),
        email_rate_limit=_env_int("EMAIL_RATE_LIMIT", 5),
        screenshot_rate_limit=_env_int("SCREENSHOT_RATE_LIMIT", 10),
        db_path=os.getenv("DB_PATH", "db.sqlite"),
        people_path=os.getenv("PEOPLE_PATH", "personas/people.yaml"),
        reply_to_rejected_dm=_env_bool("REPLY_TO_REJECTED_DM", True),
        smtp_host=os.getenv("SMTP_HOST", ""),
        smtp_port=_env_int("SMTP_PORT", 587),
        smtp_username=os.getenv("SMTP_USERNAME", ""),
        smtp_password=os.getenv("SMTP_PASSWORD", ""),
        smtp_from=os.getenv("SMTP_FROM", ""),
        smtp_default_bcc=os.getenv("SMTP_DEFAULT_BCC", ""),
        smtp_use_tls=_env_bool("SMTP_USE_TLS", True),
        http_timeout_seconds=_env_int("HTTP_TIMEOUT_SECONDS", 30),
    )


settings = load_settings()
