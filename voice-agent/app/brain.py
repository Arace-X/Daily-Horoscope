"""The agent's brain: talks to the caller and writes the call summary, using Claude."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Literal, Protocol

import anthropic
from pydantic import BaseModel, Field

from .config import Settings

log = logging.getLogger(__name__)

END_MARKER = "[END_CALL]"
FALLBACK_BETA = "server-side-fallback-2026-07-01"
# Models that accept server-side refusal fallbacks (`fallbacks: "default"`).
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

    api_messages: list = field(default_factory=list)  # exactly what is sent to Claude, append-only
    caller_turns: int = 0
    wrap_up_sent: bool = False


class Brain(Protocol):
    def greeting(self) -> str: ...

    async def reply(self, convo: Conversation, caller_text: str) -> Reply: ...

    async def summarize(self, transcript: list[dict], caller_number: str) -> CallSummary | None: ...


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


def clean_for_speech(text: str) -> str:
    text = text.replace(END_MARKER, "")
    text = re.sub(r"<[^>]+>", " ", text)  # stray tags
    text = re.sub(r"[*_#`>|]", "", text)  # markdown symbols
    return re.sub(r"\s+", " ", text).strip()


class ClaudeBrain:
    def __init__(self, settings: Settings, client: anthropic.AsyncAnthropic | None = None):
        self.settings = settings
        # max_retries=1 and a short timeout keep us inside Twilio's 15 s webhook limit.
        self.client = client or anthropic.AsyncAnthropic(
            timeout=settings.llm_timeout_seconds, max_retries=1
        )
        owner = settings.owner_name
        self._greeting = (
            f"Hi, you've reached {owner}'s phone. I'm {settings.agent_name}, "
            f"an AI assistant. {owner[:1].upper() + owner[1:]} can't take your call right now, "
            "so I'm taking messages, and this call may be recorded. "
            "May I have your name and the reason for your call?"
        )
        self.system = build_system_prompt(settings, self._greeting, settings.load_instructions())

    def greeting(self) -> str:
        return self._greeting

    def _extra_args(self) -> dict:
        if self.settings.claude_model in FALLBACK_MODELS:
            return {"betas": [FALLBACK_BETA], "fallbacks": "default"}
        return {}

    async def reply(self, convo: Conversation, caller_text: str) -> Reply:
        convo.api_messages.append({"role": "user", "content": caller_text})
        convo.caller_turns += 1
        if convo.caller_turns >= self.settings.max_turns and not convo.wrap_up_sent:
            convo.api_messages.append(
                {
                    "role": "system",
                    "content": "The call has gone on long. Wrap up now: confirm the message, "
                    f"say goodbye, and end with {END_MARKER}.",
                }
            )
            convo.wrap_up_sent = True

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
        lines = "\n".join(
            f"{'Caller' if t['role'] == 'caller' else self.settings.agent_name}: {t['text']}"
            for t in transcript
        )
        response = await self.client.with_options(timeout=60.0, max_retries=2).beta.messages.parse(
            model=self.settings.claude_model,
            max_tokens=4096,
            system=(
                f"You write short, accurate call notes for {self.settings.owner_name}, whose AI "
                "assistant answered a phone call for them. Use only what the transcript says."
            ),
            messages=[
                {
                    "role": "user",
                    "content": f"Caller's phone number: {caller_number}\n\nTranscript:\n{lines}",
                }
            ],
            output_format=CallSummary,
            output_config={"effort": "low"},
            **self._extra_args(),
        )
        if response.stop_reason == "refusal":
            return None
        return response.parsed_output
