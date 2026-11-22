"""Start everything with one command:   python start.py

1. Starts ngrok (if it isn't running already) and reads your public https address.
2. Saves it as PUBLIC_BASE_URL and points your Twilio number at it.
3. Runs the agent until you press Ctrl+C.

Use `python start.py --no-ngrok` on a cloud server that has its own public address.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys

import uvicorn

from app.config import BASE_DIR, Settings
from app.setup_tools import (
    SetupError,
    connect_twilio,
    ngrok_public_url,
    ngrok_tunnels,
    set_env_value,
    wait_for_ngrok_url,
)

NGROK_LOG = BASE_DIR / "ngrok.log"


def find_ngrok() -> str | None:
    for candidate in (BASE_DIR / "ngrok.exe", BASE_DIR / "ngrok"):
        if candidate.is_file():
            return str(candidate)
    return shutil.which("ngrok")


def last_ngrok_error() -> str:
    try:
        lines = NGROK_LOG.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    errors = [line for line in lines if "err=" in line or "ERR_NGROK" in line]
    return errors[-1] if errors else ""


def start_ngrok(port: int) -> tuple[subprocess.Popen | None, str]:
    url = ngrok_public_url(port)
    if url:
        print(f"Using the ngrok tunnel that's already running: {url}")
        return None, url
    if ngrok_tunnels():
        raise SetupError(
            "ngrok is already running for a different port. Close it, then run start.py again."
        )
    exe = find_ngrok()
    if exe is None:
        raise SetupError(
            "ngrok isn't installed. Install it (Windows: winget install ngrok.ngrok   "
            "Mac: brew install ngrok, or download from https://ngrok.com/download), "
            "connect your account once with: ngrok config add-authtoken <your token>, "
            "then run start.py again."
        )
    print("Starting ngrok...")
    NGROK_LOG.unlink(missing_ok=True)
    proc = subprocess.Popen(
        [exe, "http", str(port), "--log", str(NGROK_LOG)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    url = wait_for_ngrok_url(port, seconds=25, still_running=lambda: proc.poll() is None)
    if url is None:
        proc.terminate()
        detail = last_ngrok_error()
        hint = ""
        if "authtoken" in detail.lower() or "authentication" in detail.lower() or "4018" in detail:
            hint = (" Connect your ngrok account once with: ngrok config add-authtoken <your token> "
                    "(the token is at https://dashboard.ngrok.com/get-started/your-authtoken).")
        elif "108" in detail or "simultaneous" in detail.lower():
            hint = " Another ngrok is running somewhere. Close it, then try again."
        raise SetupError(f"ngrok didn't start.{hint}" + (f"\n  ngrok said: {detail}" if detail else ""))
    print(f"ngrok is running: {url}")
    return proc, url


def main() -> int:
    parser = argparse.ArgumentParser(description="Start the voice agent.")
    parser.add_argument("--no-ngrok", action="store_true", help="don't start ngrok (cloud servers)")
    args = parser.parse_args()

    if not (BASE_DIR / ".env").exists():
        print("There's no .env file yet. Copy .env.example to .env and fill it in first (README Part 1).")
        return 1

    settings = Settings.from_env()
    port = int(os.getenv("PORT") or "8000")
    host = os.getenv("HOST") or "127.0.0.1"
    ngrok_proc = None
    try:
        public_url = settings.public_base_url
        if not args.no_ngrok:
            ngrok_proc, public_url = start_ngrok(port)
            if public_url != settings.public_base_url:
                set_env_value("PUBLIC_BASE_URL", public_url)
            os.environ["PUBLIC_BASE_URL"] = public_url  # the server reads this when it starts

        number = settings.twilio_phone_number
        if public_url and settings.twilio_account_sid and settings.twilio_auth_token:
            try:
                number = connect_twilio(settings, public_url)
                if not settings.twilio_phone_number:
                    set_env_value("TWILIO_PHONE_NUMBER", number)
                    os.environ["TWILIO_PHONE_NUMBER"] = number
                print(f"Your Twilio number {number} now sends calls to this agent.")
            except SetupError as exc:
                print(f"Warning: couldn't connect your Twilio number: {exc}")
        else:
            print("Twilio isn't set up yet, so no phone number is connected (README Part 2).")

        print("\nYour agent is running." + (f" Call {number} to talk to it." if number else ""))
        print("Leave this window open. Press Ctrl+C to stop.\n")
        uvicorn.run("app.main:create_app", factory=True, host=host, port=port)
        return 0
    except SetupError as exc:
        print(f"Problem: {exc}")
        return 1
    finally:
        if ngrok_proc is not None:
            ngrok_proc.terminate()


if __name__ == "__main__":
    sys.exit(main())
