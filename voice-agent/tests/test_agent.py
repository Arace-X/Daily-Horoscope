"""Tests for the voice agent. No phone, Twilio account or Claude key is needed."""

from __future__ import annotations

import asyncio
import json

import anthropic
import httpx2
import pytest
from fastapi.testclient import TestClient
from twilio.request_validator import RequestValidator

from app.brain import CallSummary, ClaudeBrain, Conversation, Reply
from app.config import Settings
from app.main import create_app
from app.notify import format_message
from app.store import CallStore

# ---------------------------------------------------------------- fakes


class FakeBrain:
    def __init__(self, replies):
        self.replies = list(replies)
        self.heard: list[str] = []
        self.summarized: list[list[dict]] = []

    def greeting(self) -> str:
        return "Hi, this is Ava."

    async def reply(self, convo: Conversation, caller_text: str) -> Reply:
        self.heard.append(caller_text)
        return self.replies.pop(0)

    async def summarize(self, transcript, caller_number):
        self.summarized.append(transcript)
        return CallSummary(
            caller_name="Priya",
            reason="Rescheduling dinner",
            urgency="normal",
            callback_requested=True,
            best_time_to_call_back="After 7 pm",
            message="Priya wants to move dinner to Saturday.",
        )


class FakeNotifier:
    def __init__(self):
        self.sent: list[dict] = []

    def channels(self):
        return ["fake"]

    def send(self, call):
        self.sent.append(call)


def make_settings(tmp_path, **overrides) -> Settings:
    values = dict(
        owner_name="Sam",
        agent_name="Ava",
        database_path=tmp_path / "calls.db",
        instructions_file=tmp_path / "none.md",
        validate_twilio_signature=False,
        twilio_auth_token="test-token",
        public_base_url="https://agent.example.com",
    )
    values.update(overrides)
    return Settings(**values)


@pytest.fixture
def setup(tmp_path):
    def _make(replies=(), **overrides):
        settings = make_settings(tmp_path, **overrides)
        brain = FakeBrain(replies)
        notifier = FakeNotifier()
        store = CallStore(settings.database_path)
        app = create_app(settings, brain=brain, notifier=notifier, store=store)
        return TestClient(app), brain, notifier, store

    return _make


# ---------------------------------------------------------------- call flow


def test_incoming_call_greets_and_listens(setup):
    client, *_ = setup()
    r = client.post("/voice/incoming", data={"CallSid": "CA1", "From": "+15550001111"})
    assert r.status_code == 200
    assert "<Gather" in r.text and 'input="speech"' in r.text
    assert "Hi, this is Ava." in r.text
    assert "/voice/respond" in r.text


def test_full_conversation_then_summary_and_notification(setup):
    client, brain, notifier, store = setup(
        replies=[
            Reply("Thanks Priya. Anything else?", end_call=False),
            Reply("I'll tell Sam. Goodbye!", end_call=True),
        ]
    )
    client.post("/voice/incoming", data={"CallSid": "CA2", "From": "+15550002222"})

    r = client.post("/voice/respond", data={"CallSid": "CA2", "SpeechResult": "It's Priya, about dinner"})
    assert "Thanks Priya" in r.text and "<Gather" in r.text

    r = client.post("/voice/respond", data={"CallSid": "CA2", "SpeechResult": "No, that's all"})
    assert "<Hangup" in r.text and "Goodbye" in r.text

    # Agent hung up -> summary + notification ran in the background.
    assert len(notifier.sent) == 1
    call = notifier.sent[0]
    assert call["summary"]["caller_name"] == "Priya"
    assert [t["role"] for t in call["transcript"]] == ["agent", "caller", "agent", "caller", "agent"]

    # Twilio's status callback afterwards must not notify twice.
    client.post("/voice/status", data={"CallSid": "CA2", "CallStatus": "completed"})
    assert len(notifier.sent) == 1
    assert store.get("CA2")["summary"]["reason"] == "Rescheduling dinner"


def test_caller_hangs_up_mid_call(setup):
    client, _, notifier, _ = setup(replies=[Reply("Sure, go on.", end_call=False)])
    client.post("/voice/incoming", data={"CallSid": "CA3", "From": "+15550003333"})
    client.post("/voice/respond", data={"CallSid": "CA3", "SpeechResult": "Hi it's Raj"})
    client.post("/voice/status", data={"CallSid": "CA3", "CallStatus": "completed"})
    assert len(notifier.sent) == 1


def test_silence_reprompts_then_hangs_up(setup):
    client, _, notifier, _ = setup()
    client.post("/voice/incoming", data={"CallSid": "CA4", "From": "+15550004444"})
    for _ in range(2):
        r = client.post("/voice/no-input", data={"CallSid": "CA4"})
        assert "<Gather" in r.text
    r = client.post("/voice/no-input", data={"CallSid": "CA4"})
    assert "<Hangup" in r.text
    assert len(notifier.sent) == 1


def test_slow_claude_reply_asks_caller_to_repeat(setup):
    client, brain, _, _ = setup(llm_timeout_seconds=0.01)

    async def slow_reply(convo, text):
        await asyncio.sleep(2)

    brain.reply = slow_reply
    client.post("/voice/incoming", data={"CallSid": "CA5", "From": "+1555"})
    r = client.post("/voice/respond", data={"CallSid": "CA5", "SpeechResult": "hello"})
    assert "say it again" in r.text and "<Gather" in r.text


# ---------------------------------------------------------------- security


def test_rejects_requests_without_valid_twilio_signature(setup):
    client, *_ = setup(validate_twilio_signature=True)
    r = client.post("/voice/incoming", data={"CallSid": "CA6", "From": "+1"},
                    headers={"X-Twilio-Signature": "forged"})
    assert r.status_code == 403


def test_accepts_requests_with_valid_twilio_signature(setup):
    client, *_ = setup(validate_twilio_signature=True)
    params = {"CallSid": "CA7", "From": "+15550007777"}
    sig = RequestValidator("test-token").compute_signature(
        "https://agent.example.com/voice/incoming", params
    )
    r = client.post("/voice/incoming", data=params, headers={"X-Twilio-Signature": sig})
    assert r.status_code == 200


def test_dashboard_is_off_without_password_and_protected_with_one(setup):
    client, *_ = setup()
    assert client.get("/calls").status_code == 404

    client, *_ = setup(dashboard_password="s3cret")
    assert client.get("/calls").status_code == 401
    assert client.get("/calls", auth=("me", "wrong")).status_code == 401
    r = client.get("/calls", auth=("me", "s3cret"))
    assert r.status_code == 200 and "Calls answered by Ava" in r.text


def test_dashboard_escapes_caller_text(setup):
    client, *_ = setup(replies=[Reply("ok", end_call=False)], dashboard_password="pw")
    client.post("/voice/incoming", data={"CallSid": "CA8", "From": "+1"})
    client.post("/voice/respond", data={"CallSid": "CA8", "SpeechResult": "<script>alert(1)</script>"})
    r = client.get("/calls", auth=("me", "pw"))
    assert "<script>alert(1)</script>" not in r.text
    assert "&lt;script&gt;" in r.text


# ---------------------------------------------------------------- notification text


def test_notification_text_with_and_without_summary():
    call = {
        "from_number": "+15550009999",
        "transcript": [{"role": "caller", "text": "Call me back please"}],
        "summary": {
            "caller_name": "Dad", "reason": "Family", "urgency": "high",
            "callback_requested": True, "best_time_to_call_back": "ASAP", "message": "Call home.",
        },
    }
    text = format_message(call)
    assert text.startswith("URGENT - Missed call from Dad (+15550009999)")
    assert "Wants a call back: ASAP" in text

    call["summary"] = None
    assert "Call me back please" in format_message(call)


# ---------------------------------------------------------------- Claude requests (mocked HTTP)


def claude_with_responses(responses: list[dict], seen: list[dict]) -> anthropic.AsyncAnthropic:
    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append({"headers": dict(request.headers), "body": json.loads(request.content)})
        return httpx2.Response(200, json=responses.pop(0))

    return anthropic.AsyncAnthropic(
        api_key="test-key",
        http_client=anthropic.DefaultAsyncHttpxClient(transport=httpx2.MockTransport(handler)),
    )


def api_message(content: list[dict], stop_reason: str = "end_turn") -> dict:
    return {
        "id": "msg_1", "type": "message", "role": "assistant", "model": "claude-opus-5-5",
        "content": content, "stop_reason": stop_reason, "stop_sequence": None,
        "usage": {"input_tokens": 10, "output_tokens": 5},
    }


def test_claude_brain_request_and_end_marker(tmp_path):
    seen: list[dict] = []
    thinking = {"type": "thinking", "thinking": "", "signature": "sig"}
    client = claude_with_responses(
        [
            api_message([thinking, {"type": "text", "text": "Got it, **Priya**. Anything else?"}]),
            api_message([{"type": "text", "text": "I'll tell Sam. Goodbye! [END_CALL]"}]),
        ],
        seen,
    )
    brain = ClaudeBrain(make_settings(tmp_path), client=client)
    convo = Conversation()

    first = asyncio.run(brain.reply(convo, "It's Priya"))
    assert first == Reply("Got it, Priya. Anything else?", end_call=False)

    second = asyncio.run(brain.reply(convo, "That's all"))
    assert second == Reply("I'll tell Sam. Goodbye!", end_call=True)

    body = seen[1]["body"]
    assert body["model"] == "claude-opus-5-5"
    assert body["fallbacks"] == "default"
    assert "server-side-fallback-2026-07-01" in seen[1]["headers"]["anthropic-beta"]
    assert body["output_config"] == {"effort": "low"}
    assert "thinking" not in body  # Opus 5.5 rejects disabling thinking; we leave it at default
    # History is append-only and keeps the thinking block from the first reply.
    assert [m["role"] for m in body["messages"]] == ["user", "assistant", "user"]
    assert body["messages"][1]["content"][0]["type"] == "thinking"
    assert "Sam" in body["system"] and "[END_CALL]" in body["system"]


def test_claude_brain_handles_refusal(tmp_path):
    seen: list[dict] = []
    client = claude_with_responses([api_message([], stop_reason="refusal")], seen)
    brain = ClaudeBrain(make_settings(tmp_path), client=client)
    convo = Conversation()
    reply = asyncio.run(brain.reply(convo, "something"))
    assert "can't help with that" in reply.text and not reply.end_call
    assert [m["role"] for m in convo.api_messages] == ["user"]


def test_claude_brain_wraps_up_long_calls(tmp_path):
    seen: list[dict] = []
    client = claude_with_responses([api_message([{"type": "text", "text": "Bye [END_CALL]"}])], seen)
    brain = ClaudeBrain(make_settings(tmp_path, max_turns=1), client=client)
    asyncio.run(brain.reply(Conversation(), "hi"))
    messages = seen[0]["body"]["messages"]
    assert messages[-1]["role"] == "system" and "Wrap up" in messages[-1]["content"]


def test_cheaper_model_skips_fallbacks(tmp_path):
    seen: list[dict] = []
    client = claude_with_responses([api_message([{"type": "text", "text": "Hi"}])], seen)
    brain = ClaudeBrain(make_settings(tmp_path, claude_model="claude-haiku-5-5"), client=client)
    asyncio.run(brain.reply(Conversation(), "hi"))
    assert "fallbacks" not in seen[0]["body"]


def test_claude_brain_summary_uses_structured_output(tmp_path):
    seen: list[dict] = []
    summary = {
        "caller_name": "Priya", "reason": "Dinner", "urgency": "normal",
        "callback_requested": False, "best_time_to_call_back": "Not specified",
        "message": "Dinner moved to Saturday.",
    }
    client = claude_with_responses([api_message([{"type": "text", "text": json.dumps(summary)}])], seen)
    brain = ClaudeBrain(make_settings(tmp_path), client=client)
    transcript = [{"role": "agent", "text": "Hi"}, {"role": "caller", "text": "It's Priya, dinner is Saturday"}]
    result = asyncio.run(brain.summarize(transcript, "+1555"))
    assert result.caller_name == "Priya"
    assert seen[0]["body"]["output_config"]["format"]["type"] == "json_schema"
    assert seen[0]["body"]["output_config"]["effort"] == "low"


def test_no_summary_call_when_caller_said_nothing(tmp_path):
    brain = ClaudeBrain(make_settings(tmp_path), client=claude_with_responses([], []))
    assert asyncio.run(brain.summarize([{"role": "agent", "text": "Hi"}], "+1")) is None
