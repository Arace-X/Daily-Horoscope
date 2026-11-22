"""Setup helper for the voice agent.

    python setup_helper.py check                 # check everything, say what to fix next
    python setup_helper.py set KEY VALUE         # save a setting in .env, e.g. set GROQ_API_KEY gsk_...
    python setup_helper.py connect-twilio        # point your Twilio number at this agent
    python setup_helper.py telegram-chat-id      # find and save your Telegram chat id
    python setup_helper.py test-notify           # send a test message to your summary channels
"""

from __future__ import annotations

import argparse
import os
import sys

from app.config import Settings
from app.setup_tools import (
    ENV_PATH,
    SetupError,
    connect_twilio,
    ngrok_public_url,
    run_checks,
    set_env_value,
    telegram_chat_id,
)

MARKS = {"ok": "[ OK ]", "fix": "[FIX ]", "info": "[INFO]"}
SECRET_WORDS = ("KEY", "TOKEN", "PASSWORD", "SECRET")


def cmd_check(_args) -> int:
    settings = Settings.from_env()
    print("Checking your voice agent setup...\n")
    checks = run_checks(settings)
    for check in checks:
        print(f"{MARKS[check.status]}  {check.title}: {check.detail}")
    todo = [c for c in checks if c.status == "fix"]
    print()
    if todo:
        print(f"Next step: {todo[0].title}. {todo[0].detail}")
        return 1
    print("Everything is set up.")
    return 0


def cmd_set(args) -> int:
    set_env_value(args.key, args.value)
    shown = "(hidden)" if any(w in args.key for w in SECRET_WORDS) else args.value
    print(f"Saved {args.key} = {shown} in {ENV_PATH}")
    return 0


def cmd_connect_twilio(args) -> int:
    settings = Settings.from_env()
    port = int(os.getenv("PORT") or "8000")
    url = (args.url or ngrok_public_url(port) or settings.public_base_url).rstrip("/")
    if not url:
        raise SetupError("No public address. Start ngrok (or start.py) first, or pass --url https://...")
    number = connect_twilio(settings, url)
    if url != settings.public_base_url:
        set_env_value("PUBLIC_BASE_URL", url)
    if not settings.twilio_phone_number:
        set_env_value("TWILIO_PHONE_NUMBER", number)
    print(f"Done: calls to {number} now go to {url}/voice/incoming")
    if url != settings.public_base_url:
        print("PUBLIC_BASE_URL changed, so restart the agent (run.py) if it's running.")
    return 0


def cmd_telegram_chat_id(_args) -> int:
    settings = Settings.from_env()
    if not settings.telegram_bot_token:
        raise SetupError("Save your bot token first: setup_helper.py set TELEGRAM_BOT_TOKEN <token from @BotFather>")
    chat_id = telegram_chat_id(settings.telegram_bot_token)
    if chat_id is None:
        raise SetupError("Your bot has no messages yet. Open your bot in Telegram, send it any message, then run this again.")
    set_env_value("TELEGRAM_CHAT_ID", chat_id)
    print(f"Saved TELEGRAM_CHAT_ID = {chat_id}")
    return 0


def cmd_test_notify(_args) -> int:
    from app.notify import Notifier

    notifier = Notifier(Settings.from_env())
    if not notifier.channels():
        raise SetupError("No summary channel is set up yet (Telegram or email). See Part 3 of the README.")
    results = notifier.send_text(
        "Test from your voice agent: missed-call summaries will arrive here."
    )
    failed = False
    for channel, error in results.items():
        print(f"{channel}: {'sent' if error is None else 'FAILED - ' + error}")
        failed = failed or error is not None
    return 1 if failed else 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Setup helper for the voice agent.")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("check", help="check everything and say what to fix next").set_defaults(run=cmd_check)
    p_set = sub.add_parser("set", help="save a setting in .env")
    p_set.add_argument("key")
    p_set.add_argument("value")
    p_set.set_defaults(run=cmd_set)
    p_tw = sub.add_parser("connect-twilio", help="point your Twilio number at this agent")
    p_tw.add_argument("--url", default="", help="public https address (default: the running ngrok address)")
    p_tw.set_defaults(run=cmd_connect_twilio)
    sub.add_parser("telegram-chat-id", help="find and save your Telegram chat id").set_defaults(run=cmd_telegram_chat_id)
    sub.add_parser("test-notify", help="send a test summary message").set_defaults(run=cmd_test_notify)

    args = parser.parse_args()
    try:
        return args.run(args)
    except SetupError as exc:
        print(f"Problem: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
