"""Sends the call summary to you by Telegram, email and/or SMS (whichever are configured)."""

from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage

import httpx

from .config import Settings

log = logging.getLogger(__name__)


def format_message(call: dict) -> str:
    number = call.get("from_number") or "an unknown number"
    summary = call.get("summary")
    if summary:
        urgent = "URGENT - " if summary.get("urgency") == "high" else ""
        lines = [
            f"{urgent}Missed call from {summary.get('caller_name', 'Unknown')} ({number})",
            f"Reason: {summary.get('reason', '')}",
            f"Message: {summary.get('message', '')}",
        ]
        if summary.get("callback_requested"):
            lines.append(f"Wants a call back: {summary.get('best_time_to_call_back', 'Not specified')}")
        return "\n".join(lines)

    said = [t["text"] for t in call.get("transcript", []) if t["role"] == "caller"]
    if not said:
        return f"Missed call from {number}. The caller hung up without leaving a message."
    return f"Missed call from {number}. What they said:\n" + "\n".join(f"- {s}" for s in said)


class Notifier:
    def __init__(self, settings: Settings):
        self.s = settings

    def channels(self) -> list[str]:
        enabled = []
        if self.s.telegram_bot_token and self.s.telegram_chat_id:
            enabled.append("telegram")
        if self.s.smtp_host and self.s.email_to:
            enabled.append("email")
        if self.s.notify_sms_to and self.s.twilio_account_sid and self.s.twilio_phone_number:
            enabled.append("sms")
        return enabled

    def send(self, call: dict) -> None:
        text = format_message(call)
        senders = {"telegram": self._telegram, "email": self._email, "sms": self._sms}
        channels = self.channels()
        if not channels:
            log.info("No notification channel is configured. Summary:\n%s", text)
        for name in channels:
            try:
                senders[name](text)
                log.info("Sent call summary by %s", name)
            except Exception:  # one failing channel must not block the others
                log.exception("Could not send call summary by %s", name)

    def _telegram(self, text: str) -> None:
        resp = httpx.post(
            f"https://api.telegram.org/bot{self.s.telegram_bot_token}/sendMessage",
            json={"chat_id": self.s.telegram_chat_id, "text": text},
            timeout=15,
        )
        resp.raise_for_status()

    def _email(self, text: str) -> None:
        msg = EmailMessage()
        msg["Subject"] = text.splitlines()[0][:120]
        msg["From"] = self.s.smtp_user or self.s.email_to
        msg["To"] = self.s.email_to
        msg.set_content(text)
        with smtplib.SMTP(self.s.smtp_host, self.s.smtp_port, timeout=20) as smtp:
            smtp.starttls()
            if self.s.smtp_user:
                smtp.login(self.s.smtp_user, self.s.smtp_password)
            smtp.send_message(msg)

    def _sms(self, text: str) -> None:
        from twilio.rest import Client

        Client(self.s.twilio_account_sid, self.s.twilio_auth_token).messages.create(
            to=self.s.notify_sms_to, from_=self.s.twilio_phone_number, body=text[:1500]
        )
