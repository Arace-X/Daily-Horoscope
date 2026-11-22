"""Automates the fiddly setup steps, so a person (or Claude in Cowork) doesn't click through
settings pages: editing .env, reading the ngrok address, pointing the Twilio number at this
server, finding your Telegram chat id, and checking that everything works."""

from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass
from pathlib import Path

import httpx

from .config import BASE_DIR, Settings

ENV_PATH = BASE_DIR / ".env"
NGROK_API = "http://127.0.0.1:4040/api/tunnels"


class SetupError(Exception):
    """A setup step failed; the message says what to do in plain words."""


# ---------------------------------------------------------------- .env file


def set_env_value(key: str, value: str, path: Path = ENV_PATH) -> None:
    """Sets KEY=value in the .env file (creating it from .env.example if needed)."""
    if not re.fullmatch(r"[A-Z][A-Z0-9_]*", key):
        raise SetupError(f"{key!r} isn't a setting name. Use capitals, like GROQ_API_KEY.")
    value = value.strip()
    if "\n" in value or "\r" in value:
        raise SetupError("A setting can't contain line breaks.")
    if re.search(r"[\s#'\"\\]", value):  # quote values with spaces, # or quotes
        value = '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'

    if not path.exists():
        example = path.with_name(".env.example")
        path.write_text(example.read_text(encoding="utf-8") if example.exists() else "", encoding="utf-8")
    lines = path.read_text(encoding="utf-8").splitlines()
    pattern = re.compile(rf"^\s*{re.escape(key)}\s*=")
    for i, line in enumerate(lines):
        if pattern.match(line):
            lines[i] = f"{key}={value}"
            break
    else:
        lines.append(f"{key}={value}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ---------------------------------------------------------------- ngrok


def ngrok_tunnels(http: httpx.Client | None = None) -> list[dict] | None:
    """Tunnels of the ngrok program running on this computer, or None if it isn't running."""
    client = http or httpx.Client(timeout=2, trust_env=False)
    try:
        return client.get(NGROK_API).json().get("tunnels", [])
    except (httpx.HTTPError, ValueError, AttributeError):
        return None


def ngrok_public_url(port: int, http: httpx.Client | None = None) -> str | None:
    """The https address ngrok gives to the given local port, if ngrok is running."""
    for tunnel in ngrok_tunnels(http) or []:
        url = str(tunnel.get("public_url", ""))
        addr = str((tunnel.get("config") or {}).get("addr", ""))
        if url.startswith("https://") and (addr.endswith(f":{port}") or addr == str(port)):
            return url.rstrip("/")
    return None


def wait_for_ngrok_url(port: int, seconds: float = 20, still_running=lambda: True) -> str | None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline and still_running():
        url = ngrok_public_url(port)
        if url:
            return url
        time.sleep(0.5)
    return None


# ---------------------------------------------------------------- Twilio


def twilio_client(settings: Settings):
    if not (settings.twilio_account_sid and settings.twilio_auth_token):
        raise SetupError("Add TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN to .env first.")
    from twilio.rest import Client

    return Client(settings.twilio_account_sid, settings.twilio_auth_token)


def find_twilio_number(client, wanted: str):
    """The account's phone number record: the one in .env, or the only one there is."""
    from twilio.base.exceptions import TwilioRestException

    try:
        if wanted:
            found = client.incoming_phone_numbers.list(phone_number=wanted, limit=1)
            return found[0] if found else None
        numbers = client.incoming_phone_numbers.list(limit=2)
    except TwilioRestException as exc:
        if exc.status in (401, 403):
            raise SetupError(
                "Twilio rejected your Account SID or Auth Token. Copy both again from the "
                "Twilio Console home page."
            ) from exc
        raise SetupError(f"Twilio error: {exc.msg}") from exc
    return numbers[0] if len(numbers) == 1 else None


def webhook_urls(base_url: str) -> tuple[str, str]:
    base = base_url.rstrip("/")
    return f"{base}/voice/incoming", f"{base}/voice/status"


def connect_twilio(settings: Settings, base_url: str, client=None) -> str:
    """Points the Twilio number's call webhooks at this server. Returns the phone number."""
    if not base_url.startswith("https://"):
        raise SetupError("The public address must start with https:// (start ngrok first).")
    client = client or twilio_client(settings)
    number = find_twilio_number(client, settings.twilio_phone_number)
    if number is None:
        if settings.twilio_phone_number:
            raise SetupError(
                f"{settings.twilio_phone_number} isn't in your Twilio account. Check "
                "TWILIO_PHONE_NUMBER in .env (format: +15551234567)."
            )
        raise SetupError(
            "Couldn't tell which Twilio number to use. Get a number in the Twilio Console, "
            "or put it in .env as TWILIO_PHONE_NUMBER."
        )
    voice_url, status_url = webhook_urls(base_url)
    client.incoming_phone_numbers(number.sid).update(
        voice_url=voice_url,
        voice_method="POST",
        status_callback=status_url,
        status_callback_method="POST",
    )
    return number.phone_number


def place_test_call(settings: Settings, base_url: str, to: str = "", client=None) -> str:
    """Has the Twilio number call the user's phone and connect them to the agent.

    Free to the user (paid from Twilio trial credit), unlike calling a US number from abroad.
    `to` defaults to the account's only verified phone number. Returns the number called.
    """
    from twilio.base.exceptions import TwilioRestException

    if not base_url.startswith("https://"):
        raise SetupError("Start the agent first (start.py), so it has a public https address.")
    client = client or twilio_client(settings)
    number = find_twilio_number(client, settings.twilio_phone_number)
    if number is None:
        raise SetupError("No Twilio number found. Get a trial number in the Twilio Console first.")
    to = to.replace(" ", "").replace("-", "")
    try:
        if not to:
            verified = client.outgoing_caller_ids.list(limit=2)
            if len(verified) != 1:
                raise SetupError("Say which phone to call: setup_helper.py test-call --to +91XXXXXXXXXX")
            to = verified[0].phone_number
        voice_url, status_url = webhook_urls(base_url)
        client.calls.create(
            to=to,
            from_=number.phone_number,
            url=voice_url,
            method="POST",
            status_callback=status_url,
            status_callback_method="POST",
        )
    except TwilioRestException as exc:
        hint = ""
        if exc.code in (21215, 13227) or "geo" in str(exc.msg).lower() or "permission" in str(exc.msg).lower():
            hint = (" Turn on calls to your country in the Twilio Console: Voice > Settings > "
                    "Geo permissions, then try again.")
        elif exc.code == 21219 or "verified" in str(exc.msg).lower():
            hint = " On a trial, Twilio can only call phone numbers you've verified."
        raise SetupError(f"Twilio couldn't place the call: {exc.msg}.{hint}") from exc
    return to


# ---------------------------------------------------------------- Telegram


def telegram_chat_id(token: str, http: httpx.Client | None = None) -> str | None:
    """The chat id of the latest message sent to your bot (send it a message first)."""
    client = http or httpx.Client(timeout=15)
    try:
        resp = client.get(f"https://api.telegram.org/bot{token}/getUpdates")
    except httpx.HTTPError as exc:
        raise SetupError("Couldn't reach Telegram. Check your internet connection.") from exc
    if resp.status_code in (401, 404):
        raise SetupError("Telegram rejected the bot token. Copy it again from @BotFather.")
    try:
        updates = resp.json().get("result", [])
    except ValueError as exc:
        raise SetupError("Telegram sent an unexpected reply. Try again in a minute.") from exc
    for update in reversed(updates):
        for key in ("message", "edited_message", "channel_post", "my_chat_member"):
            chat = (update.get(key) or {}).get("chat") or {}
            if "id" in chat:
                return str(chat["id"])
    return None


# ---------------------------------------------------------------- checks


@dataclass
class Check:
    status: str  # "ok", "fix" or "info"
    title: str
    detail: str = ""


def _check_brain(settings: Settings) -> Check:
    from .brain import BrainError, make_brain

    provider = settings.llm_provider
    title = f"AI brain ({provider})"
    if provider == "groq" and not settings.llm_api_key:
        return Check("fix", title, "No GROQ_API_KEY yet. Get a free key at https://console.groq.com/keys")
    try:
        brain = make_brain(settings)
        asyncio.run(brain.ping())
    except ValueError as exc:
        return Check("fix", title, str(exc))
    except BrainError as exc:
        if exc.status in (401, 403):
            return Check("fix", title, "The API key was rejected. Copy it again into .env.")
        if exc.status == 404 and provider == "ollama":
            return Check("fix", title, "Ollama doesn't have the model yet. Run: ollama pull llama3.2")
        if exc.status is None and provider == "ollama":
            return Check("fix", title, "Can't reach Ollama. Open the Ollama app, then check again.")
        if exc.status is None:
            return Check("fix", title, f"Can't reach {provider}. Check your internet connection. ({exc})")
        if exc.status == 429:
            return Check("fix", title, "The free tier's limit is used up for now. Wait a minute, then check again.")
        return Check("fix", title, str(exc))
    except Exception as exc:  # e.g. a missing or rejected ANTHROPIC_API_KEY
        return Check("fix", title, f"{exc.__class__.__name__}: {exc}")
    return Check("ok", title, f"{brain.label} answered")


def _check_public_url(settings: Settings, http: httpx.Client | None) -> Check:
    title = "Public address"
    url = settings.public_base_url
    if not url:
        return Check("fix", title, "Not set yet. Run start.py (it starts ngrok and saves the address).")
    if not url.startswith("https://"):
        return Check("fix", title, f"{url} must start with https://")
    client = http or httpx.Client(timeout=10)
    try:
        ok = client.get(f"{url}/health").json().get("ok") is True
    except (httpx.HTTPError, ValueError, AttributeError):
        ok = False
    if not ok:
        return Check("fix", title, f"{url} isn't answering. Is start.py (or run.py and ngrok) running?")
    return Check("ok", title, f"{url} is reachable")


def _check_twilio(settings: Settings, twilio) -> Check:
    title = "Twilio number"
    if not (settings.twilio_account_sid and settings.twilio_auth_token):
        return Check("fix", title, "Add TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN from the Twilio Console.")
    try:
        client = twilio or twilio_client(settings)
        number = find_twilio_number(client, settings.twilio_phone_number)
    except SetupError as exc:
        return Check("fix", title, str(exc))
    if number is None:
        return Check("fix", title, "No number found. Get a trial number in the Twilio Console.")
    if getattr(number, "voice_application_sid", None):
        return Check("fix", title, f"{number.phone_number} is attached to a TwiML App, which overrides "
                     "the webhook. Remove it in the Twilio Console (Voice Configuration).")
    if not settings.public_base_url:
        return Check("info", title, f"{number.phone_number} found. It gets connected when you run start.py.")
    voice_url, status_url = webhook_urls(settings.public_base_url)
    if number.voice_url != voice_url or number.status_callback != status_url:
        return Check("fix", title, f"{number.phone_number} isn't pointed at this agent yet. "
                     "Run: setup_helper.py connect-twilio (start.py does it automatically).")
    return Check("ok", title, f"{number.phone_number} sends calls to this agent")


def _check_notifications(settings: Settings) -> Check:
    from .notify import Notifier

    title = "Call summaries"
    if settings.telegram_bot_token and not settings.telegram_chat_id:
        return Check("fix", title, "Send your Telegram bot a message, then run: setup_helper.py telegram-chat-id")
    channels = Notifier(settings).channels()
    if not channels:
        return Check("fix", title, "No Telegram or email set up yet, so summaries only appear in the log.")
    return Check("ok", title, "Sent by " + " and ".join(channels) + " (test with: setup_helper.py test-notify)")


def run_checks(
    settings: Settings,
    *,
    env_path: Path = ENV_PATH,
    http: httpx.Client | None = None,
    twilio=None,
    online: bool = True,
) -> list[Check]:
    checks = [
        Check("ok", "Settings file", str(env_path)) if env_path.exists()
        else Check("fix", "Settings file", "No .env yet. Run: setup_helper.py set OWNER_NAME YourName"),
        Check("ok", "Your name", settings.owner_name) if settings.owner_name != "the owner"
        else Check("fix", "Your name", "Run: setup_helper.py set OWNER_NAME YourFirstName"),
    ]
    if online:
        checks.append(_check_brain(settings))
        checks.append(_check_twilio(settings, twilio))
        checks.append(_check_public_url(settings, http))
    checks.append(_check_notifications(settings))
    if not settings.validate_twilio_signature:
        checks.append(Check("fix", "Security", "VALIDATE_TWILIO_SIGNATURE is off. Turn it back on (true)."))
    return checks
