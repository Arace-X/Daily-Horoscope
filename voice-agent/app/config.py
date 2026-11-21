"""Settings, read from environment variables (or a .env file)."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

load_dotenv(BASE_DIR / ".env")


def _bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int) -> int:
    value = os.getenv(name, "").strip()
    return int(value) if value else default


def _float(name: str, default: float) -> float:
    value = os.getenv(name, "").strip()
    return float(value) if value else default


def _str(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


@dataclass
class Settings:
    # Who the agent works for
    owner_name: str = "the owner"
    agent_name: str = "Ava"
    instructions_file: Path = BASE_DIR / "agent_instructions.md"

    # Claude
    claude_model: str = "claude-opus-5-5"
    claude_effort: str = "low"  # low keeps replies fast enough for a phone call
    llm_timeout_seconds: float = 11.0  # Twilio gives up on a webhook after 15 s
    max_turns: int = 12  # caller turns before the agent wraps up the call

    # Twilio / phone
    public_base_url: str = ""  # e.g. https://abcd.ngrok-free.app (no trailing slash)
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_phone_number: str = ""
    validate_twilio_signature: bool = True
    call_language: str = "en-US"  # speech recognition language, e.g. en-IN, hi-IN
    tts_voice: str = "Polly.Joanna"  # Twilio <Say> voice, e.g. Polly.Aditi for Hindi

    # Where call summaries go (all optional; set any you want)
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    email_to: str = ""
    notify_sms_to: str = ""

    # Storage and dashboard
    database_path: Path = BASE_DIR / "calls.db"
    dashboard_password: str = ""  # empty = dashboard turned off

    @classmethod
    def from_env(cls) -> "Settings":
        instructions = _str("AGENT_INSTRUCTIONS_FILE")
        database = _str("DATABASE_PATH")
        return cls(
            owner_name=_str("OWNER_NAME", "the owner"),
            agent_name=_str("AGENT_NAME", "Ava"),
            instructions_file=Path(instructions) if instructions else BASE_DIR / "agent_instructions.md",
            claude_model=_str("CLAUDE_MODEL", "claude-opus-5-5"),
            claude_effort=_str("CLAUDE_EFFORT", "low"),
            llm_timeout_seconds=_float("LLM_TIMEOUT_SECONDS", 11.0),
            max_turns=_int("MAX_TURNS", 12),
            public_base_url=_str("PUBLIC_BASE_URL").rstrip("/"),
            twilio_account_sid=_str("TWILIO_ACCOUNT_SID"),
            twilio_auth_token=_str("TWILIO_AUTH_TOKEN"),
            twilio_phone_number=_str("TWILIO_PHONE_NUMBER"),
            validate_twilio_signature=_bool("VALIDATE_TWILIO_SIGNATURE", True),
            call_language=_str("CALL_LANGUAGE", "en-US"),
            tts_voice=_str("TTS_VOICE", "Polly.Joanna"),
            telegram_bot_token=_str("TELEGRAM_BOT_TOKEN"),
            telegram_chat_id=_str("TELEGRAM_CHAT_ID"),
            smtp_host=_str("SMTP_HOST"),
            smtp_port=_int("SMTP_PORT", 587),
            smtp_user=_str("SMTP_USER"),
            smtp_password=_str("SMTP_PASSWORD"),
            email_to=_str("EMAIL_TO"),
            notify_sms_to=_str("NOTIFY_SMS_TO"),
            database_path=Path(database) if database else BASE_DIR / "calls.db",
            dashboard_password=_str("DASHBOARD_PASSWORD"),
        )

    def load_instructions(self) -> str:
        try:
            text = self.instructions_file.read_text(encoding="utf-8")
        except FileNotFoundError:
            return ""
        return re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL).strip()
