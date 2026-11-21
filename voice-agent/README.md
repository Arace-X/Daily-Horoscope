# Voice Agent: an AI that answers your calls when you can't

When you miss a call, your phone forwards it to this agent. The agent tells the
caller it's an AI assistant, has a natural conversation to find out who they are
and what they want, then sends you a short summary by Telegram, email or SMS.

```
Someone calls you ─► you don't answer ─► your carrier forwards the call ─► Twilio number
                                                                              │
         you get: "Missed call from Priya (+91…): wants to move dinner        ▼
         to Saturday. Call back after 7 pm."  ◄── summary ◄── this server ◄── speech-to-text
                                                       │  ▲
                                                       ▼  │ Claude writes each reply
                                                  caller hears the reply (text-to-speech)
```

- **Phone side:** [Twilio](https://www.twilio.com) handles the phone number, speech recognition and the voice.
- **Brain:** [Claude](https://www.anthropic.com/api) runs the conversation and writes the summary.
- **This app:** a small Python web server that connects the two, keeps a call log and notifies you.

## What it costs

| Item | Cost |
|---|---|
| This code, Telegram and email notifications, ngrok for testing | Free |
| Twilio | The free trial credit covers building and testing. After that, about $1–2/month for a number plus a few cents per minute (check Twilio's pricing for your country) |
| Claude API | Pay per use. `claude-opus-5-5` (the default) costs a few cents per call. Set `CLAUDE_MODEL=claude-haiku-5-5` and it costs a small fraction of a cent per call |
| Call forwarding | Depends on your carrier. Some bill forwarded minutes like outgoing calls |

---

## Setup

### 1. Install

You need Python 3.10 or newer.

```bash
cd voice-agent
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env             # then open .env and fill it in as you go
```

### 2. Get a Claude API key

1. Sign up at <https://console.anthropic.com>, add a little credit, and create an API key.
2. Put it in `.env` as `ANTHROPIC_API_KEY=...`, and set `OWNER_NAME` to your name.
3. Edit `agent_instructions.md` to tell the agent about you. It can share anything in that
   file with callers, so only include things you're fine with callers hearing.

### 3. Try it in your terminal first (no phone needed)

```bash
python simulate.py
```

Pretend to be a caller. When you type `bye`, it prints the summary you would get.
Adjust `agent_instructions.md` until you like how it sounds.

### 4. Get a Twilio phone number

1. Sign up at <https://www.twilio.com/try-twilio>. The trial gives you free credit.
2. In the Console, go to **Phone Numbers → Buy a number** and get one with **Voice** capability.
   - Pick a number in **your own country** if you can. Forwarding your calls to a foreign
     number is billed as an international call. Some countries ask for ID documents before
     you can get a local number.
3. Copy your **Account SID**, **Auth Token** and the new number into `.env`.

Trial limits: callers hear a short Twilio trial message first, and only numbers you've verified
in the Twilio Console can call. Upgrading the account removes both limits.

### 5. Run the server and make it reachable

Twilio has to be able to reach your server over the internet. For testing, use
[ngrok](https://ngrok.com) (free):

```bash
# terminal 1
uvicorn app.main:create_app --factory --port 8000

# terminal 2
ngrok http 8000
```

Copy the `https://….ngrok-free.app` address ngrok shows into `.env` as `PUBLIC_BASE_URL`
(no trailing slash), then restart the server.

### 6. Point your Twilio number at the server

In the Twilio Console, go to **Phone Numbers → Active numbers → (your number)**. Under **Voice Configuration**, set:

| Setting | Value |
|---|---|
| A call comes in | Webhook, `https://YOUR-URL/voice/incoming`, HTTP POST |
| Call status changes | `https://YOUR-URL/voice/status`, HTTP POST |

Save, then **call your Twilio number**. The agent should answer.

### 7. Forward your own phone's missed calls to it

On most GSM networks you set this up by dialling a code from your phone (replace the number with your
Twilio number, including the country code):

| When | Dial | To turn off |
|---|---|---|
| You don't answer | `**61*+1234567890#` | `##61#` |
| You're busy or reject the call | `**67*+1234567890#` | `##67#` |
| Your phone is off or out of coverage | `**62*+1234567890#` | `##62#` |

You can also find this in your phone's settings (**Phone → Settings → Call forwarding**).
Some carriers use different codes or charge for forwarded minutes, so check with yours.
On iPhone, "unconditional" forwarding is in Settings, and the conditional codes above
usually work too.

### 8. Get the summaries

Fill in any of these in `.env`:

- **Telegram (free):** message [@BotFather](https://t.me/BotFather), send `/newbot`, and copy the token
  into `TELEGRAM_BOT_TOKEN`. Send your new bot any message, then open
  `https://api.telegram.org/bot<TOKEN>/getUpdates` and copy `"chat":{"id": ...}` into `TELEGRAM_CHAT_ID`.
- **Email (free):** for Gmail, use `SMTP_HOST=smtp.gmail.com`, `SMTP_PORT=587`, your address as
  `SMTP_USER`, and an [App Password](https://myaccount.google.com/apppasswords) as `SMTP_PASSWORD`.
- **SMS:** set `NOTIFY_SMS_TO` to your mobile number. It's sent from your Twilio number and costs a little per message.

If none are set, summaries are printed in the server log.

### 9. Call log dashboard

Set `DASHBOARD_PASSWORD` and open `https://YOUR-URL/calls`. Any username works with that password.
It lists every call with its summary and full transcript, and urgent calls are highlighted.

---

## Running it all the time

ngrok and your laptop are fine for testing. For a 24/7 agent, deploy the folder to any host
that runs Python, such as a small VPS, Render, Railway or Fly.io. Then:

- Start command: `uvicorn app.main:create_app --factory --host 0.0.0.0 --port $PORT`
- Set the same variables as in `.env` in the host's settings.
- Set `PUBLIC_BASE_URL` to the host's https address and update the two Twilio webhooks.
- The call log is a SQLite file (`calls.db`). Use a host with a persistent disk, or point
  `DATABASE_PATH` at a mounted volume, or the log resets on each redeploy.
- Free tiers that "sleep" when idle make the first call slow to answer, so use an always-on plan.

## Settings you might want to change

| Variable | What it does |
|---|---|
| `CLAUDE_MODEL` | `claude-opus-5-5` (default, most natural) or `claude-haiku-5-5` (cheapest, slightly faster) |
| `CLAUDE_EFFORT` | `low` (default) keeps replies quick. Higher is more thorough but slower, and phone callers notice pauses |
| `CALL_LANGUAGE` / `TTS_VOICE` | For example `en-IN` + `Polly.Raveena`, or `hi-IN` + `Polly.Aditi` for Hindi. Twilio's docs list every voice |
| `MAX_TURNS` | How many times the caller can speak before the agent starts wrapping up (default 12) |
| `AGENT_NAME` | What the assistant calls itself |

## Privacy and the law

- The agent says up front that it's an AI and that the call may be recorded. Many places
  require this, so keep that in the greeting.
- Transcripts are stored in `calls.db` on your server and are sent to Claude and Twilio to
  run the call. Don't put anything in `agent_instructions.md` that you wouldn't want a caller to hear.
- The server checks Twilio's signature on every webhook, so random people can't fake calls.
  Keep `VALIDATE_TWILIO_SIGNATURE=true` outside of local testing.

## Troubleshooting

| Problem | Fix |
|---|---|
| Call says "an application error has occurred" | Check the server log. The most common cause is a wrong `PUBLIC_BASE_URL` (it must exactly match what you entered in Twilio), which makes the signature check reject the request with a 403 |
| Agent says "Sorry, I missed that" a lot | Claude took longer than ~11 s. Keep `CLAUDE_EFFORT=low`, or try `CLAUDE_MODEL=claude-haiku-5-5` |
| Calls never reach the agent | Check that forwarding is on (`*#61#` shows the status on many phones) and that you dialled the Twilio number with its country code |
| No summary arrives | Set the **Call status changes** webhook. Check `/health`, which lists the notification channels it found |

## Development

```bash
pip install -r requirements-dev.txt
pytest
```

The tests fake Twilio and Claude, so they need no accounts or keys.

| File | Purpose |
|---|---|
| `app/main.py` | Twilio webhooks, call flow, dashboard |
| `app/brain.py` | Claude prompt, conversation turns, summary |
| `app/notify.py` | Telegram, email and SMS |
| `app/store.py` | SQLite call log |
| `app/config.py` | Settings from `.env` |
| `simulate.py` | Chat with the agent in your terminal |
