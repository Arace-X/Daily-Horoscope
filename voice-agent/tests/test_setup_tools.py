"""Tests for the setup automation. No real Twilio, ngrok or Telegram is used."""

from __future__ import annotations

import json

import httpx
import pytest
from dotenv import dotenv_values
from twilio.base.exceptions import TwilioRestException

from app.config import Settings
from app.notify import Notifier
from app.setup_tools import (
    SetupError,
    connect_twilio,
    ngrok_public_url,
    run_checks,
    set_env_value,
    telegram_chat_id,
)

# ---------------------------------------------------------------- .env editing


def test_set_env_value_replaces_appends_and_keeps_comments(tmp_path):
    env = tmp_path / ".env"
    env.write_text("# my settings\nOWNER_NAME=\nGROQ_API_KEY=old\n", encoding="utf-8")
    set_env_value("GROQ_API_KEY", "gsk_new", env)
    set_env_value("TELEGRAM_CHAT_ID", "12345", env)
    text = env.read_text(encoding="utf-8")
    assert text.startswith("# my settings\n")
    assert "GROQ_API_KEY=gsk_new\n" in text and "old" not in text
    assert dotenv_values(env)["TELEGRAM_CHAT_ID"] == "12345"


def test_set_env_value_creates_file_from_example(tmp_path):
    (tmp_path / ".env.example").write_text("OWNER_NAME=\nAGENT_NAME=Ava\n", encoding="utf-8")
    env = tmp_path / ".env"
    set_env_value("OWNER_NAME", "Sam", env)
    assert dotenv_values(env) == {"OWNER_NAME": "Sam", "AGENT_NAME": "Ava"}


def test_set_env_value_quotes_tricky_values(tmp_path):
    env = tmp_path / ".env"
    set_env_value("SMTP_PASSWORD", 'abcd efgh #1 "x"', env)
    assert dotenv_values(env)["SMTP_PASSWORD"] == 'abcd efgh #1 "x"'


def test_set_env_value_rejects_bad_input(tmp_path):
    with pytest.raises(SetupError):
        set_env_value("not a key", "x", tmp_path / ".env")
    with pytest.raises(SetupError):
        set_env_value("OWNER_NAME", "line1\nline2", tmp_path / ".env")


# ---------------------------------------------------------------- ngrok


def ngrok_api(tunnels=None, down=False) -> httpx.Client:
    def handler(request):
        if down:
            raise httpx.ConnectError("connection refused")
        assert request.url.path == "/api/tunnels"
        return httpx.Response(200, json={"tunnels": tunnels or []})

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_ngrok_public_url_picks_https_tunnel_for_our_port():
    tunnels = [
        {"public_url": "http://abc.ngrok-free.app", "config": {"addr": "http://localhost:8000"}},
        {"public_url": "https://other.ngrok-free.app", "config": {"addr": "http://localhost:9000"}},
        {"public_url": "https://abc.ngrok-free.app", "config": {"addr": "http://localhost:8000"}},
    ]
    assert ngrok_public_url(8000, ngrok_api(tunnels)) == "https://abc.ngrok-free.app"
    assert ngrok_public_url(7000, ngrok_api(tunnels)) is None


def test_ngrok_not_running():
    assert ngrok_public_url(8000, ngrok_api(down=True)) is None


# ---------------------------------------------------------------- Twilio


class FakeNumber:
    def __init__(self, phone_number, voice_url="", status_callback="", voice_application_sid=None):
        self.sid = "PN" + phone_number[-4:]
        self.phone_number = phone_number
        self.voice_url = voice_url
        self.status_callback = status_callback
        self.voice_application_sid = voice_application_sid


class FakeNumbers:
    def __init__(self, numbers, error=None):
        self.numbers, self.error, self.updates = numbers, error, []

    def list(self, phone_number=None, limit=None):
        if self.error:
            raise self.error
        found = [n for n in self.numbers if phone_number in (None, n.phone_number)]
        return found[:limit]

    def __call__(self, sid):
        outer = self

        class Context:
            def update(self, **fields):
                outer.updates.append((sid, fields))

        return Context()


class FakeTwilio:
    def __init__(self, numbers, error=None):
        self.incoming_phone_numbers = FakeNumbers(numbers, error)


def twilio_settings(**overrides) -> Settings:
    values = dict(twilio_account_sid="AC123", twilio_auth_token="tok", twilio_phone_number="")
    values.update(overrides)
    return Settings(**values)


def test_connect_twilio_sets_both_webhooks_on_the_only_number():
    client = FakeTwilio([FakeNumber("+15550001234")])
    number = connect_twilio(twilio_settings(), "https://abc.ngrok-free.app/", client)
    assert number == "+15550001234"
    assert client.incoming_phone_numbers.updates == [(
        "PN1234",
        {
            "voice_url": "https://abc.ngrok-free.app/voice/incoming",
            "voice_method": "POST",
            "status_callback": "https://abc.ngrok-free.app/voice/status",
            "status_callback_method": "POST",
        },
    )]


def test_connect_twilio_uses_the_number_from_env_when_there_are_several():
    client = FakeTwilio([FakeNumber("+15550001111"), FakeNumber("+15550002222")])
    with pytest.raises(SetupError):  # ambiguous without TWILIO_PHONE_NUMBER
        connect_twilio(twilio_settings(), "https://x.ngrok-free.app", client)
    number = connect_twilio(twilio_settings(twilio_phone_number="+15550002222"), "https://x.ngrok-free.app", client)
    assert number == "+15550002222"


def test_connect_twilio_explains_problems():
    with pytest.raises(SetupError, match="https"):
        connect_twilio(twilio_settings(), "http://localhost:8000", FakeTwilio([FakeNumber("+15550001234")]))
    with pytest.raises(SetupError, match="isn't in your Twilio account"):
        connect_twilio(twilio_settings(twilio_phone_number="+19999999999"), "https://x.app",
                       FakeTwilio([FakeNumber("+15550001234")]))
    bad_login = TwilioRestException(status=401, uri="/IncomingPhoneNumbers.json", msg="Authenticate")
    with pytest.raises(SetupError, match="Account SID or Auth Token"):
        connect_twilio(twilio_settings(), "https://x.app", FakeTwilio([], error=bad_login))
    with pytest.raises(SetupError, match="TWILIO_ACCOUNT_SID"):
        connect_twilio(Settings(), "https://x.app")


# ---------------------------------------------------------------- Telegram


def telegram_api(status=200, payload=None) -> httpx.Client:
    def handler(request):
        assert request.url.path.endswith("/getUpdates")
        return httpx.Response(status, json=payload if payload is not None else {"ok": True, "result": []})

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_telegram_chat_id_from_latest_message():
    payload = {"ok": True, "result": [
        {"update_id": 1, "message": {"chat": {"id": 111}}},
        {"update_id": 2, "message": {"chat": {"id": 222}}},
    ]}
    assert telegram_chat_id("123:abc", telegram_api(payload=payload)) == "222"
    assert telegram_chat_id("123:abc", telegram_api()) is None
    with pytest.raises(SetupError, match="rejected the bot token"):
        telegram_chat_id("bad", telegram_api(status=401, payload={"ok": False}))


def test_telegram_failure_message_does_not_leak_token(monkeypatch):
    def fake_post(url, **kwargs):
        return httpx.Response(401, json={"ok": False, "description": "Unauthorized"},
                              request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", fake_post)
    notifier = Notifier(Settings(telegram_bot_token="123:SECRET", telegram_chat_id="5"))
    results = notifier.send_text("hi")
    assert results["telegram"] is not None
    assert "SECRET" not in results["telegram"] and "Unauthorized" in results["telegram"]


# ---------------------------------------------------------------- checks


def test_checks_say_what_to_fix(tmp_path):
    checks = run_checks(Settings(), env_path=tmp_path / ".env", online=False)
    fixes = {c.title for c in checks if c.status == "fix"}
    assert {"Settings file", "Your name", "Call summaries"} <= fixes


def test_checks_online_with_fakes(tmp_path):
    env = tmp_path / ".env"
    env.write_text("OWNER_NAME=Sam\n", encoding="utf-8")

    def health(request):
        assert str(request.url) == "https://abc.ngrok-free.app/health"
        return httpx.Response(200, json={"ok": True})

    settings = twilio_settings(owner_name="Sam", public_base_url="https://abc.ngrok-free.app",
                               telegram_bot_token="t", telegram_chat_id="1")
    good = FakeNumber("+15550001234", "https://abc.ngrok-free.app/voice/incoming",
                      "https://abc.ngrok-free.app/voice/status")
    checks = {c.title: c for c in run_checks(
        settings, env_path=env, twilio=FakeTwilio([good]),
        http=httpx.Client(transport=httpx.MockTransport(health)),
    )}
    assert checks["AI brain (groq)"].status == "fix"  # no key, so no network call is made
    assert checks["Twilio number"].status == "ok"
    assert checks["Public address"].status == "ok"
    assert checks["Call summaries"].status == "ok"

    stale = FakeNumber("+15550001234", "https://old.ngrok-free.app/voice/incoming")
    checks = {c.title: c for c in run_checks(
        settings, env_path=env, twilio=FakeTwilio([stale]),
        http=httpx.Client(transport=httpx.MockTransport(health)),
    )}
    assert checks["Twilio number"].status == "fix" and "connect-twilio" in checks["Twilio number"].detail


def test_brain_ping_sends_a_tiny_request(tmp_path):
    import asyncio

    from app.brain import OpenAICompatibleBrain

    seen = []

    def handler(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": "OK"}}]})

    brain = OpenAICompatibleBrain(
        Settings(llm_api_key="gsk_x", instructions_file=tmp_path / "none.md"),
        http=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    asyncio.run(brain.ping())
    assert seen[0]["max_completion_tokens"] == 50
    assert seen[0]["messages"] == [{"role": "user", "content": "Reply with the word OK."}]


# ---------------------------------------------------------------- test call (Twilio calls the user)

from types import SimpleNamespace  # noqa: E402

from app.setup_tools import place_test_call  # noqa: E402


class CallingTwilio(FakeTwilio):
    def __init__(self, numbers, verified, error=None):
        super().__init__(numbers)
        self.created = []
        self.outgoing_caller_ids = SimpleNamespace(
            list=lambda limit=None: [SimpleNamespace(phone_number=v) for v in verified][:limit]
        )

        def create(**kwargs):
            if error:
                raise error
            self.created.append(kwargs)

        self.calls = SimpleNamespace(create=create)


def test_test_call_rings_the_only_verified_phone():
    client = CallingTwilio([FakeNumber("+15550001234")], verified=["+919800000000"])
    called = place_test_call(twilio_settings(), "https://abc.ngrok-free.app", client=client)
    assert called == "+919800000000"
    assert client.created == [{
        "to": "+919800000000",
        "from_": "+15550001234",
        "url": "https://abc.ngrok-free.app/voice/incoming",
        "method": "POST",
        "status_callback": "https://abc.ngrok-free.app/voice/status",
        "status_callback_method": "POST",
    }]


def test_test_call_needs_a_clear_target_and_explains_geo_permissions():
    two = CallingTwilio([FakeNumber("+15550001234")], verified=["+919800000000", "+919800000001"])
    with pytest.raises(SetupError, match="--to"):
        place_test_call(twilio_settings(), "https://x.app", client=two)
    assert place_test_call(twilio_settings(), "https://x.app", to="+91 98000 00001", client=two) == "+919800000001"

    geo = TwilioRestException(status=400, uri="/Calls.json", code=21215,
                              msg="Account not authorized to call +919800000000")
    blocked = CallingTwilio([FakeNumber("+15550001234")], verified=["+919800000000"], error=geo)
    with pytest.raises(SetupError, match="Geo permissions"):
        place_test_call(twilio_settings(), "https://x.app", client=blocked)
    with pytest.raises(SetupError, match="Start the agent"):
        place_test_call(twilio_settings(), "", client=blocked)
