# Voice Agent: an AI that answers your calls when you can't

When you miss a call, your phone forwards it to this agent. The agent tells the
caller it's an AI assistant, has a natural conversation to find out who they are
and what they want, then sends you a short summary on Telegram or by email.

```
Someone calls you ─► you don't answer ─► your carrier forwards the call ─► Twilio number
                                                                              │
         you get: "Missed call from Priya (+1…): wants to move dinner          ▼
         to Saturday. Call back after 7 pm."  ◄── summary ◄── this server ◄── speech-to-text
                                                       │  ▲
                                                       ▼  │ a free AI writes each reply
                                                  caller hears the reply (text-to-speech)
```

## What's free and what isn't

| Part | Cost |
|---|---|
| This code | Free |
| The AI brain: [Groq](https://console.groq.com)'s free tier (default) or [Ollama](https://ollama.com) on your own computer | Free |
| Running it on your computer, with [ngrok](https://ngrok.com) to make it reachable | Free |
| Summaries on Telegram or by email | Free |
| Twilio free trial | Free, but only for testing: only phone numbers you've verified in Twilio can call in, callers hear a trial message and press a key first, and calls end after 10 minutes |
| Letting **anyone** call it (going live) | Paid. You must upgrade Twilio. In the US that's about $1–2/month for the number, plus per-minute charges and about $0.02 per 15 seconds of speech recognized. Check [Twilio's pricing](https://www.twilio.com/en-us/voice/pricing) for your country |
| Forwarding your calls to it | Depends on your carrier. Forwarding to a number in another country is usually charged |
| Claude as the brain (optional) | Paid per use, but the most natural conversations |

There's no phone service that lets software answer calls from anyone for free, so
going live always costs a little. Everything up to that point is free.

---

## Part 1: Talk to your agent in the terminal (free)

### 1. Get the code

Download <https://github.com/Arace-X/Daily-Horoscope/archive/refs/heads/claude/lucid-goodall-7dxm4y.zip>
and unzip it. Everything is in the `voice-agent` folder inside.

### 2. Install Python

Download Python 3.12 or newer from <https://www.python.org/downloads/> and install it.
**On Windows, tick "Add python.exe to PATH"** on the first installer screen.

### 3. Open a terminal in the `voice-agent` folder

- **Windows:** open the `voice-agent` folder in File Explorer, click the address bar, type `cmd`, press Enter.
- **Mac:** open Terminal, type `cd ` (with a space), drag the `voice-agent` folder into the window, press Enter.

### 4. Install the agent

| Windows | Mac / Linux |
|---|---|
| `py -m venv .venv` | `python3 -m venv .venv` |
| `.venv\Scripts\python -m pip install -r requirements.txt` | `.venv/bin/python -m pip install -r requirements.txt` |
| `copy .env.example .env` | `cp .env.example .env` |
| `notepad .env` | `open -e .env` |

The last line opens your settings file, `.env`. Keep it private: it will hold your keys.

### 5. Get a free Groq key

1. Go to <https://console.groq.com/keys> and sign in (a Google account works).
2. Click **Create API Key** and copy it (it starts with `gsk_`).
3. In `.env`, fill in `GROQ_API_KEY=` with the key and `OWNER_NAME=` with your first name. Save.

### 6. Talk to it

| Windows | Mac / Linux |
|---|---|
| `.venv\Scripts\python simulate.py` | `.venv/bin/python simulate.py` |

Pretend to be a caller. When you type `bye`, it shows the summary you'd get.
To change what the agent knows about you, edit `agent_instructions.md` and run it again.

---

## Part 2: Answer a real phone call (free trial)

### 1. Get a free Twilio number

1. Sign up at <https://www.twilio.com/try-twilio>. You'll verify your own phone number during
   sign-up. Only verified numbers can call your agent during the trial.
2. In the Console, open **Phone Numbers → Manage → Buy a number** and pick one with **Voice**.
   It's paid from your free trial credit. US numbers are the easiest to get. Other countries
   may ask for documents.
3. Copy your **Account SID**, **Auth Token** and the new number (like `+15551234567`) into `.env`.

If you live outside the US, calling a US Twilio number from your phone may cost you
international rates, so check your plan.

### 2. Make your computer reachable with ngrok (free)

1. Sign up at <https://ngrok.com>, then download and install ngrok.
2. Run the "add authtoken" command from your ngrok dashboard (one time only).
3. In a **second** terminal window, run `ngrok http 8000`.
4. Copy the `https://….ngrok-free.app` address it shows into `.env` as `PUBLIC_BASE_URL`
   (no `/` at the end).

On the free plan the address can change when you restart ngrok. Your ngrok dashboard
may offer one free permanent domain. If it does, use it so the address stays the same.

### 3. Start the agent

| Windows | Mac / Linux |
|---|---|
| `.venv\Scripts\python run.py` | `.venv/bin/python run.py` |

Leave this window and the ngrok window open.

### 4. Connect your Twilio number

In the Twilio Console, open **Phone Numbers → Manage → Active numbers → (your number)**.
Under **Voice Configuration**, set:

| Setting | Value |
|---|---|
| A call comes in | Webhook, `https://YOUR-NGROK-ADDRESS/voice/incoming`, HTTP POST |
| Call status changes | `https://YOUR-NGROK-ADDRESS/voice/status`, HTTP POST |

Save.

### 5. Call it

Call your Twilio number from the phone you verified. You'll hear Twilio's trial message.
Press any key, and your agent answers.

---

## Part 3: Get the summaries (free)

Fill in either of these in `.env`, then restart `run.py`:

- **Telegram:** in Telegram, message [@BotFather](https://t.me/BotFather), send `/newbot`, and
  copy the token into `TELEGRAM_BOT_TOKEN`. Send your new bot any message, then open
  `https://api.telegram.org/bot<TOKEN>/getUpdates` in a browser and copy the number after
  `"chat":{"id":` into `TELEGRAM_CHAT_ID`.
- **Email:** for Gmail, use `SMTP_HOST=smtp.gmail.com`, `SMTP_PORT=587`, your address as
  `SMTP_USER` and `EMAIL_TO`, and an [App Password](https://myaccount.google.com/apppasswords)
  as `SMTP_PASSWORD`.

To check the setup, run `simulate.py --notify`. It sends the test summary to you.

---

## Part 4: Go live (paid)

Only do this if you want anyone to be able to reach the agent.

1. **Upgrade Twilio** (Console → Billing). Any remaining trial credit goes away when you upgrade.
2. **Forward your missed calls.** On most GSM networks, dial these codes from your phone,
   using your Twilio number with its country code:

   | When | Dial | To turn off |
   |---|---|---|
   | You don't answer | `**61*+15551234567#` | `##61#` |
   | You're busy or reject the call | `**67*+15551234567#` | `##67#` |
   | Your phone is off or has no signal | `**62*+15551234567#` | `##62#` |

   You can also set this up in your phone's call settings. Some carriers use different
   codes or charge for forwarded minutes, so check with yours.

## Part 5: Keep it running

The agent only answers while `run.py` and ngrok are running, so your computer must stay
on and connected. For an always-on agent, move it to a small cloud server:

- Start command: `python run.py`, with `HOST=0.0.0.0` (and `PORT` if the host gives you one).
- Copy your `.env` values into the host's settings, set `PUBLIC_BASE_URL` to the server's
  https address, and update the two Twilio webhooks.
- Hosts that put free apps to sleep when idle are too slow to wake up for a phone call.

---

## Choosing the AI brain

Set `LLM_PROVIDER` in `.env`:

| Value | Cost | Notes |
|---|---|---|
| `groq` (default) | Free tier | Fast, which matters on a phone call. Needs `GROQ_API_KEY`. Free-tier limits apply; see your Groq console |
| `ollama` | Free | Runs on your own computer, no account. Install from <https://ollama.com>, then run `ollama pull llama3.2`. Slower on computers without a good graphics card |
| `claude` | Paid per use | Most natural. Needs `ANTHROPIC_API_KEY` from <https://platform.claude.com/settings/keys>. `CLAUDE_MODEL=claude-haiku-5-5` is the cheapest |
| `custom` | Varies | Any OpenAI-compatible API. Set `LLM_BASE_URL`, `LLM_MODEL` and `LLM_API_KEY` |

`LLM_MODEL` changes the model within a provider (for example `openai/gpt-oss-20b` on Groq
for even faster replies, or `qwen3` on Ollama).

## Other settings

| Variable | What it does |
|---|---|
| `CALL_LANGUAGE` / `TTS_VOICE` | For example `en-IN` + `Polly.Raveena`, or `hi-IN` + `Polly.Aditi` for Hindi |
| `MAX_TURNS` | How many times the caller can speak before the agent wraps up (default 12) |
| `AGENT_NAME` | What the assistant calls itself |
| `DASHBOARD_PASSWORD` | Turns on the call log page at `https://YOUR-ADDRESS/calls` (any username) |

## Privacy and the law

- The agent says up front that it's an AI and that the call may be recorded. Many places
  require this, so keep that in the greeting.
- Transcripts are stored in `calls.db` on your computer and sent to the AI service and
  Twilio to run the call. Don't put anything in `agent_instructions.md` that you wouldn't want a caller to hear.
- Never share or upload your `.env` file. It's excluded from git.
- The server checks Twilio's signature on every webhook, so nobody else can fake calls to it.

## Troubleshooting

| Problem | Fix |
|---|---|
| The call says "an application error has occurred" | Check the `run.py` window. Most often `PUBLIC_BASE_URL` doesn't exactly match the address in Twilio, so the request is rejected with a 403 |
| Twilio says the call can't be completed | On a trial, the calling number must be verified in Twilio (Phone Numbers → Verified Caller IDs) |
| The agent says "Sorry, I missed that" a lot | The AI took too long. On Ollama, try a smaller model. On Groq, try `LLM_MODEL=openai/gpt-oss-20b` |
| "You've hit the free tier's limit" | Groq's free limits reset after a minute or a day. Wait, or switch to Ollama |
| No summary arrives | Set the **Call status changes** webhook, and check `https://YOUR-ADDRESS/health`, which lists the channels it found |

## Development

```bash
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m pytest
```

The tests fake Twilio and the AI services, so they need no accounts or keys.

| File | Purpose |
|---|---|
| `app/main.py` | Twilio webhooks, call flow, dashboard |
| `app/brain.py` | AI prompts and providers (Groq, Ollama, Claude), call summary |
| `app/notify.py` | Telegram, email and SMS |
| `app/store.py` | SQLite call log |
| `app/config.py` | Settings from `.env` |
| `run.py` | Starts the server |
| `simulate.py` | Chat with the agent in your terminal |
