"""The agent's brain: talks to the caller and writes the call summary.

Two kinds of brain, picked with LLM_PROVIDER in .env:
- OpenAICompatibleBrain (free): Groq's free tier (the default), or Ollama running on your
  own computer, or any other OpenAI-compatible API ("custom").
- ClaudeBrain (paid per use): Claude, for the most natural conversations.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass, field
from typing import Literal, Protocol

import anthropic
import httpx
from pydantic import BaseModel, Field, ValidationError

from .config import Settings

log = logging.getLogger(__name__)

END_MARKER = "[END_CALL]"

# Free (OpenAI-compatible) providers and their default models.
PROVIDERS = {
    "groq": {"base_url": "https://api.groq.com/openai/v1", "model": "openai/gpt-oss-120b"},
    "ollama": {"base_url": "http://localhost:11434/v1", "model": "llama3.2"},
    "custom": {"base_url": "", "model": ""},
}

# Claude: models that accept server-side refusal fallbacks (`fallbacks: "default"`).
FALLBACK_BETA = "server-side-fallback-2026-07-01"
FALLBACK_MODELS = {
    "claude-fable-5-2",
    "claude-fable-5-1",
    "claude-opus-5-5",
    "claude-opus-5",
    "claude-sonnet-5-5",
}


class CallSummary(BaseModel):
    caller_name: str = Field(description="The caller's name as they gave it, or 'Unknown'.")
    reason: str = Field(description="One short line saying why they called.")
    urgency: Literal["low", "normal", "high"] = Field(
        description="high only if the caller said it is urgent or an emergency."
    )
    callback_requested: bool = Field(description="True if the caller wants a call back.")
    best_time_to_call_back: str = Field(
        description="When the caller said is best to call back, or 'Not specified'."
    )
    message: str = Field(description="The caller's message for the owner, in 1-4 plain sentences.")


@dataclass
class Reply:
    text: str
    end_call: bool


@dataclass
class Conversation:
    """Conversation state for one live call."""

    api_messages: list = field(default_factory=list)  # what is sent to the AI, append-only
    caller_turns: int = 0
    wrap_up_sent: bool = False


class Brain(Protocol):
    label: str

    def greeting(self) -> str: ...

    async def reply(self, convo: Conversation, caller_text: str) -> Reply: ...

    async def summarize(self, transcript: list[dict], caller_number: str) -> CallSummary | None: ...


class BrainError(Exception):
    """The AI service failed or returned something unusable."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


# ---------------------------------------------------------------- shared prompt pieces


def make_greeting(settings: Settings) -> str:
    owner = settings.owner_name
    return (
        f"Hi, you've reached {owner}'s phone. I'm {settings.agent_name}, "
        f"an AI assistant. {owner[:1].upper() + owner[1:]} can't take your call right now, "
        "so I'm taking messages, and this call may be recorded. "
        "May I have your name and the reason for your call?"
    )


def build_system_prompt(settings: Settings, greeting: str, extra_instructions: str) -> str:
    owner = settings.owner_name
    prompt = f"""You are {settings.agent_name}, the AI phone assistant for {owner}. {owner} could not answer, so the call was forwarded to you. You are speaking with the caller on a live phone call.

The caller has already heard this greeting from you: "{greeting}"

Your job:
- Find out who is calling and why, and take a clear message for {owner}.
- Ask whether they want a call back and, if so, a good time. Their phone number is already known; do not ask for it unless they say to use a different one.
- Answer simple questions using only the information {owner} has given you below. If you do not know something, say you will pass the question on. Never invent facts, promises, prices, appointments or availability.
- If the caller says it is an emergency, tell them to contact local emergency services if anyone is in danger, and say you will mark the message as urgent.
- If someone asks, be honest that you are an AI assistant.
- Do not share private details about {owner} (address, other contacts, schedule details) unless the information below explicitly says you may.

How to speak:
- Your words are turned into speech, so write only what should be said aloud: plain conversational sentences, no lists, markdown, emojis, or internal or system XML tags.
- Keep each reply short: one to three sentences, usually one question at a time.
- Speak in the same language the caller uses.

Ending the call:
- When you have the message and the caller has nothing more to add, or they say goodbye, briefly confirm what you will tell {owner}, say goodbye, and put {END_MARKER} at the very end of that final reply.
- Use {END_MARKER} only in your final reply."""
    if extra_instructions:
        prompt += f"\n\nInformation and instructions from {owner}:\n{extra_instructions}"
    return prompt


WRAP_UP_NOTE = (
    "The call has gone on long. Wrap up now: confirm the message, say goodbye, "
    f"and end with {END_MARKER}."
)


def start_turn(convo: Conversation, caller_text: str, max_turns: int) -> None:
    """Adds the caller's words (and, on long calls, a wrap-up note) to the conversation."""
    convo.api_messages.append({"role": "user", "content": caller_text})
    convo.caller_turns += 1
    if convo.caller_turns >= max_turns and not convo.wrap_up_sent:
        convo.api_messages.append({"role": "system", "content": WRAP_UP_NOTE})
        convo.wrap_up_sent = True


def strip_think(text: str) -> str:
    """Removes <think>...</think> reasoning that some free models put in their answer."""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL | re.IGNORECASE)
    return re.sub(r"<think>.*", "", text, flags=re.DOTALL | re.IGNORECASE)  # unclosed block


def clean_for_speech(text: str) -> str:
    text = strip_think(text).replace(END_MARKER, "")
    text = re.sub(r"<[^>]+>", " ", text)  # stray tags
    text = re.sub(r"[*_#`>|]", "", text)  # markdown symbols
    return re.sub(r"\s+", " ", text).strip()


def summary_system(settings: Settings) -> str:
    return (
        f"You write short, accurate call notes for {settings.owner_name}, whose AI "
        "assistant answered a phone call for them. Use only what the transcript says."
    )


def summary_request(settings: Settings, transcript: list[dict], caller_number: str) -> str:
    lines = "\n".join(
        f"{'Caller' if t['role'] == 'caller' else settings.agent_name}: {t['text']}"
        for t in transcript
    )
    return f"Caller's phone number: {caller_number}\n\nTranscript:\n{lines}"


SUMMARY_JSON_INSTRUCTIONS = (
    "Reply with only a JSON object with exactly these keys: "
    '"caller_name" (the caller\'s name as they gave it, or "Unknown"), '
    '"reason" (one short line saying why they called), '
    '"urgency" ("low", "normal" or "high"; "high" only if the caller said it is urgent or an emergency), '
    '"callback_requested" (true or false), '
    '"best_time_to_call_back" (when the caller said is best to call back, or "Not specified"), '
    '"message" (the caller\'s message for the owner, in 1-4 plain sentences).'
)

_SUMMARY_DEFAULTS = {
    "caller_name": "Unknown",
    "reason": "",
    "urgency": "normal",
    "callback_requested": False,
    "best_time_to_call_back": "Not specified",
    "message": "",
}


def parse_summary(raw: str) -> CallSummary | None:
    """Reads the JSON summary a free model wrote. Returns None if it is unusable."""
    text = strip_think(raw)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    data = {**_SUMMARY_DEFAULTS, **{k: v for k, v in data.items() if v is not None}}
    urgency = str(data["urgency"]).strip().lower()
    data["urgency"] = urgency if urgency in {"low", "normal", "high"} else "normal"
    try:
        summary = CallSummary.model_validate(data)
    except ValidationError:
        return None
    if not (summary.message.strip() or summary.reason.strip()):
        return None
    return summary


# ---------------------------------------------------------------- free brains (Groq, Ollama, custom)


class OpenAICompatibleBrain:
    """Uses any OpenAI-compatible chat API. Groq's free tier is the default."""

    def __init__(self, settings: Settings, http: httpx.AsyncClient | None = None):
        preset = PROVIDERS.get(settings.llm_provider)
        if preset is None:
            raise ValueError(
                f"Unknown LLM_PROVIDER {settings.llm_provider!r}. Use groq, ollama, claude or custom."
            )
        self.settings = settings
        self.provider = settings.llm_provider
        self.base_url = (settings.llm_base_url or preset["base_url"]).rstrip("/")
        self.model = settings.llm_model or preset["model"]
        if not self.base_url or not self.model:
            raise ValueError("With LLM_PROVIDER=custom, set both LLM_BASE_URL and LLM_MODEL in .env.")
        self.api_key = settings.llm_api_key
        self.http = http or httpx.AsyncClient()
        self.label = f"{self.provider}:{self.model}"
        self._greeting = make_greeting(settings)
        self.system = build_system_prompt(settings, self._greeting, settings.load_instructions())

    def greeting(self) -> str:
        return self._greeting

    async def _chat(
        self, messages: list[dict], *, max_tokens: int, timeout: float, json_mode: bool = False
    ) -> str:
        body: dict = {"model": self.model, "messages": messages}
        body["max_completion_tokens" if self.provider == "groq" else "max_tokens"] = max_tokens
        if "gpt-oss" in self.model:
            body["reasoning_effort"] = "low"  # fast replies matter more than deep thought on a call
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}

        for attempt in range(2):
            try:
                resp = await self.http.post(
                    f"{self.base_url}/chat/completions", json=body, headers=headers, timeout=timeout
                )
            except httpx.HTTPError as exc:
                raise BrainError(f"Could not reach {self.base_url} ({exc.__class__.__name__}).") from exc
            if resp.status_code == 429 and attempt == 0:
                wait = _retry_after_seconds(resp)
                if wait <= 2:  # free-tier rate limit: one quick retry if the wait is short
                    await asyncio.sleep(wait)
                    continue
            if resp.status_code >= 400:
                raise BrainError(
                    f"{self.provider} returned HTTP {resp.status_code}: {resp.text[:300]}",
                    status=resp.status_code,
                )
            break

        try:
            return resp.json()["choices"][0]["message"].get("content") or ""
        except (ValueError, KeyError, IndexError, TypeError, AttributeError) as exc:
            raise BrainError(f"Unexpected response from {self.provider}: {resp.text[:300]}") from exc

    async def ping(self) -> None:
        """Raises BrainError if the AI service can't be used (bad key, no model, offline)."""
        await self._chat(
            [{"role": "user", "content": "Reply with the word OK."}], max_tokens=50, timeout=60
        )

    async def warm_up(self) -> None:
        """Ollama loads the model on first use, which can take several seconds. Do it early."""
        if self.provider != "ollama":
            return
        try:
            await self._chat(
                [{"role": "system", "content": self.system}, {"role": "user", "content": "Hello"}],
                max_tokens=1,
                timeout=60,
            )
        except BrainError:
            log.warning("Could not warm up Ollama. Is it running?")

    async def reply(self, convo: Conversation, caller_text: str) -> Reply:
        start_turn(convo, caller_text, self.settings.max_turns)
        raw = await self._chat(
            [{"role": "system", "content": self.system}, *convo.api_messages],
            max_tokens=1024,
            timeout=self.settings.llm_timeout_seconds,
        )
        visible = strip_think(raw).strip()
        text = clean_for_speech(visible)
        if not text:
            return Reply("Sorry, could you say that again?", end_call=False)
        convo.api_messages.append({"role": "assistant", "content": visible})
        return Reply(text, end_call=END_MARKER in visible)

    async def summarize(self, transcript: list[dict], caller_number: str) -> CallSummary | None:
        if not any(t["role"] == "caller" for t in transcript):
            return None
        messages = [
            {"role": "system", "content": summary_system(self.settings) + "\n\n" + SUMMARY_JSON_INSTRUCTIONS},
            {"role": "user", "content": summary_request(self.settings, transcript, caller_number)},
        ]
        try:
            raw = await self._chat(messages, max_tokens=1500, timeout=60, json_mode=True)
        except BrainError as exc:
            if exc.status != 400:
                raise
            # Some models don't support JSON mode; ask again without it.
            raw = await self._chat(messages, max_tokens=1500, timeout=60)
        return parse_summary(raw)


def _retry_after_seconds(resp: httpx.Response) -> float:
    try:
        return max(0.0, float(resp.headers.get("retry-after", "1")))
    except ValueError:
        return 1.0


# ---------------------------------------------------------------- Claude (paid)


class ClaudeBrain:
    def __init__(self, settings: Settings, client: anthropic.AsyncAnthropic | None = None):
        self.settings = settings
        # max_retries=1 and a short timeout keep us inside Twilio's 15 s webhook limit.
        self.client = client or anthropic.AsyncAnthropic(
            timeout=settings.llm_timeout_seconds, max_retries=1
        )
        self.label = f"claude:{settings.claude_model}"
        self._greeting = make_greeting(settings)
        self.system = build_system_prompt(settings, self._greeting, settings.load_instructions())

    def greeting(self) -> str:
        return self._greeting

    async def ping(self) -> None:
        """Raises if the API key or model can't be used. Costs nothing."""
        await self.client.models.retrieve(self.settings.claude_model)

    def _extra_args(self) -> dict:
        if self.settings.claude_model in FALLBACK_MODELS:
            return {"betas": [FALLBACK_BETA], "fallbacks": "default"}
        return {}

    async def reply(self, convo: Conversation, caller_text: str) -> Reply:
        start_turn(convo, caller_text, self.settings.max_turns)
        response = await self.client.beta.messages.create(
            model=self.settings.claude_model,
            max_tokens=2048,  # spoken replies are deliberately short
            system=self.system,
            messages=convo.api_messages,
            output_config={"effort": self.settings.claude_effort},
            cache_control={"type": "ephemeral"},
            **self._extra_args(),
        )

        if response.stop_reason == "refusal":
            log.warning("Claude declined a turn (category=%s)",
                        getattr(response.stop_details, "category", None))
            return Reply(
                f"I'm sorry, I can't help with that, but I'll let {self.settings.owner_name} "
                "know you called. Is there a message you'd like me to pass on?",
                end_call=False,
            )

        # Keep the full assistant content (including thinking blocks) so history stays append-only.
        convo.api_messages.append({"role": "assistant", "content": response.to_dict()["content"]})

        raw = "".join(b.text for b in response.content if b.type == "text")
        end_call = END_MARKER in raw
        text = clean_for_speech(raw)
        if not text:
            text = "Sorry, could you say that again?"
            end_call = False
        return Reply(text, end_call)

    async def summarize(self, transcript: list[dict], caller_number: str) -> CallSummary | None:
        if not any(t["role"] == "caller" for t in transcript):
            return None
        response = await self.client.with_options(timeout=60.0, max_retries=2).beta.messages.parse(
            model=self.settings.claude_model,
            max_tokens=4096,
            system=summary_system(self.settings),
            messages=[
                {"role": "user", "content": summary_request(self.settings, transcript, caller_number)}
            ],
            output_format=CallSummary,
            output_config={"effort": "low"},
            **self._extra_args(),
        )
        if response.stop_reason == "refusal":
            return None
        return response.parsed_output


def make_brain(settings: Settings) -> Brain:
    if settings.llm_provider == "claude":
        return ClaudeBrain(settings)
    return OpenAICompatibleBrain(settings)
