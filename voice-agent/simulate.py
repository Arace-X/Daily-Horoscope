"""Talk to your agent by typing, without a phone.

    python simulate.py            # chat, then print the call summary
    python simulate.py --notify   # also send the summary to your configured channels
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

import anthropic

from app.brain import BrainError, Conversation, make_brain
from app.config import BASE_DIR, Settings
from app.notify import Notifier, format_message

FAKE_NUMBER = "+10000000000 (simulated)"


def setup_problem(settings: Settings) -> str | None:
    """Explains what is missing before we start, in plain words."""
    if not (BASE_DIR / ".env").exists():
        return (
            "There is no .env file yet. Copy .env.example to .env "
            "(Windows: copy .env.example .env   Mac/Linux: cp .env.example .env), then fill it in."
        )
    provider = settings.llm_provider
    if provider == "groq" and not settings.llm_api_key:
        return (
            "Add your free Groq key to .env as GROQ_API_KEY=...  "
            "Get one at https://console.groq.com/keys"
        )
    if provider == "claude" and not os.getenv("ANTHROPIC_API_KEY"):
        return "LLM_PROVIDER is claude, so add ANTHROPIC_API_KEY=... to .env."
    if provider not in {"groq", "ollama", "claude", "custom"}:
        return f"LLM_PROVIDER={provider!r} isn't one of: groq, ollama, claude, custom."
    return None


def explain(error: Exception, settings: Settings) -> str:
    provider = settings.llm_provider
    if isinstance(error, BrainError):
        if error.status in (401, 403):
            return "Your API key was rejected. Check the key in .env (no spaces or quotes around it)."
        if error.status == 404 and provider == "ollama":
            return "Ollama doesn't have that model yet. Run:  ollama pull llama3.2"
        if error.status == 404:
            return f"The AI service doesn't know that model. Check LLM_MODEL in .env. ({error})"
        if error.status == 429:
            return "You've hit the free tier's limit for now. Wait a minute and try again."
        if error.status is None and provider == "ollama":
            return "Can't reach Ollama. Open the Ollama app (or run: ollama serve), then try again."
        if error.status is None:
            return f"Can't reach the AI service. Check your internet connection, then try again. ({error})"
        return str(error)
    if isinstance(error, anthropic.AuthenticationError):
        return "Your ANTHROPIC_API_KEY was rejected. Check it in .env."
    if isinstance(error, anthropic.APIError):
        return f"Claude API error: {error}"
    return f"{error.__class__.__name__}: {error}"


async def main(send_notification: bool) -> int:
    settings = Settings.from_env()
    problem = setup_problem(settings)
    if problem:
        print(problem)
        return 1

    try:
        brain = make_brain(settings)
    except ValueError as error:
        print(error)
        return 1
    if settings.llm_provider == "ollama":
        print("(Loading the local model, this can take a moment the first time...)")
        await brain.warm_up()
    convo = Conversation()
    transcript = [{"role": "agent", "text": brain.greeting()}]

    print(f"(Talking to {settings.agent_name}, using {brain.label}. Type 'bye' or press Ctrl+C to hang up.)\n")
    print(f"{settings.agent_name}: {brain.greeting()}")
    try:
        while True:
            said = input("You: ").strip()
            if not said:
                continue
            transcript.append({"role": "caller", "text": said})
            try:
                reply = await brain.reply(convo, said)
            except Exception as error:  # show a helpful message instead of a traceback
                print(f"\nProblem: {explain(error, settings)}")
                return 1
            transcript.append({"role": "agent", "text": reply.text})
            print(f"{settings.agent_name}: {reply.text}")
            if reply.end_call:
                print("(the agent hung up)")
                break
    except (KeyboardInterrupt, EOFError):
        print("\n(you hung up)")

    print("\nWriting the summary...")
    try:
        summary = await brain.summarize(transcript, FAKE_NUMBER)
    except Exception as error:
        print(f"Problem writing the summary: {explain(error, settings)}")
        summary = None
    call = {
        "from_number": FAKE_NUMBER,
        "transcript": transcript,
        "summary": summary.model_dump() if summary else None,
    }
    print("\n--- The notification you would get ---")
    print(format_message(call))
    if send_notification:
        Notifier(settings).send(call)
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--notify", action="store_true", help="send the summary to your channels")
    sys.exit(asyncio.run(main(parser.parse_args().notify)))
