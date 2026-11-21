"""Talk to your agent by typing, without a phone. Uses the real Claude API.

    python simulate.py            # chat, then print the call summary
    python simulate.py --notify   # also send the summary to your configured channels
"""

from __future__ import annotations

import argparse
import asyncio

from app.brain import ClaudeBrain, Conversation
from app.config import Settings
from app.notify import Notifier, format_message


async def main(send_notification: bool) -> None:
    settings = Settings.from_env()
    brain = ClaudeBrain(settings)
    convo = Conversation()
    transcript = [{"role": "agent", "text": brain.greeting()}]

    print(f"(Talking to {settings.agent_name} on {settings.claude_model}. Type 'bye' or Ctrl+C to end.)\n")
    print(f"{settings.agent_name}: {brain.greeting()}")
    try:
        while True:
            said = input("You: ").strip()
            if not said:
                continue
            transcript.append({"role": "caller", "text": said})
            reply = await brain.reply(convo, said)
            transcript.append({"role": "agent", "text": reply.text})
            print(f"{settings.agent_name}: {reply.text}")
            if reply.end_call:
                print("(agent hung up)")
                break
    except (KeyboardInterrupt, EOFError):
        print("\n(you hung up)")

    summary = await brain.summarize(transcript, "+10000000000 (simulated)")
    call = {
        "from_number": "+10000000000 (simulated)",
        "transcript": transcript,
        "summary": summary.model_dump() if summary else None,
    }
    print("\n--- Notification you would get ---")
    print(format_message(call))
    if send_notification:
        Notifier(settings).send(call)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--notify", action="store_true", help="send the summary to your channels")
    asyncio.run(main(parser.parse_args().notify))
