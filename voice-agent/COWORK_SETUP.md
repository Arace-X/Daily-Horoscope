# Set up the voice agent (instructions for Claude in Cowork)

The user asked you to set up their AI phone agent for them, for free. Do every step you can
yourself. Before each step, tell the user in one short sentence what you're doing. Ask them
only for things only they can do.

## Ground rules

- **The user signs in and types codes themselves.** Let them sign in to websites and type any
  password or verification code. Never type a password for them.
- **Keep it free.** Don't buy, upgrade or enter payment details anywhere. A Twilio trial number is
  free. Upgrading Twilio is not, so leave that decision to the user.
- **Keys and tokens are secrets.** Save them only in the `.env` file, with
  `setup_helper.py set`. Never repeat them in chat or upload `.env` anywhere.
- **Ask before installing software** (Python, ngrok), and say what it is.
- **Use the user's real computer.** The agent has to keep running after you're done, so it must be
  installed and started on the user's own Windows or macOS system, not only inside a sandbox. If
  your shell is an isolated sandbox, put the files in the user's folder and run the install and
  start steps in the user's own Terminal or Command Prompt. Use computer use for that, or give the
  user one command to paste.

In the commands below, `<python>` means `.venv\Scripts\python` on Windows and
`.venv/bin/python` on Mac or Linux. Run everything inside the `voice-agent` folder.

## Done means

1. A test conversation in the terminal works (step 4).
2. The user called their free Twilio trial number from their own phone and talked to the agent (step 7).
3. A call summary arrived in Telegram, or by email (step 8).
4. The user knows how to start the agent again, and what going live would cost (step 9).

## Step 1: Get the code

Ask which folder to use, and suggest their Documents folder. Then download and unzip:

- Windows (PowerShell):
  ```
  Invoke-WebRequest -Uri https://github.com/Arace-X/Daily-Horoscope/archive/refs/heads/claude/lucid-goodall-7dxm4y.zip -OutFile voice-agent.zip
  Expand-Archive voice-agent.zip -DestinationPath . -Force
  ```
- Mac:
  ```
  curl -L -o voice-agent.zip https://github.com/Arace-X/Daily-Horoscope/archive/refs/heads/claude/lucid-goodall-7dxm4y.zip && unzip -q -o voice-agent.zip
  ```

The agent is in the `voice-agent` folder inside the unzipped folder
(`Daily-Horoscope-claude-lucid-goodall-7dxm4y`).

## Step 2: Install

1. Python 3.10 or newer is needed. Check with `py --version` (Windows) or `python3 --version` (Mac).
   If it's missing or older, ask first, then install it:
   - Windows: `winget install -e --id Python.Python.3.12`, then open a new terminal.
   - Mac: `brew install python@3.12` if Homebrew is installed. Otherwise the user installs it from
     <https://www.python.org/downloads/> (the installer asks for their password).
2. Run the installer: `install.bat --no-pause` on Windows, `sh install.sh` on Mac. It creates
   `.venv`, installs the packages, and creates `.env`.
3. Ask the user for their first name, then run `<python> setup_helper.py set OWNER_NAME <name>`.
4. Ask whether callers should be told anything, such as when the user usually calls back. Add it
   to `agent_instructions.md` in plain sentences. Only include things the user is happy for any
   caller to hear.

## Step 3: Free AI key (Groq)

1. Open <https://console.groq.com/keys> in the browser and let the user sign in (Google sign-in is fine).
2. Click **Create API Key**, name it `voice-agent`, and copy the key (it starts with `gsk_`).
3. Run `<python> setup_helper.py set GROQ_API_KEY <key>`. If you can't read the key from the page,
   open `.env` in a text editor and ask the user to paste it after `GROQ_API_KEY=`.
4. Run `<python> setup_helper.py check`. The **AI brain** line must say OK. Other lines can still
   say FIX at this point.

## Step 4: Test conversation

Run a scripted test call:

- Mac:
  `printf 'Hi, this is Alex. Please say I will be late for dinner.\nNo, that is all. Bye.\n' | .venv/bin/python simulate.py`
- Windows (PowerShell):
  `"Hi, this is Alex. Please say I will be late for dinner.", "No, that is all. Bye." | .venv\Scripts\python simulate.py`

The agent should reply, then print **The notification you would get** with a summary. Show the user
the conversation. Offer to let them try it themselves: they run `<python> simulate.py` and type.

## Step 5: Free Twilio trial number

1. Open <https://www.twilio.com/try-twilio>. The user signs up and verifies their email and phone
   number. Twilio texts them a code, and they type it. During the trial, only that verified phone
   can call the agent.
2. If Twilio asks setup questions, choose answers like: calls/voice, Python, personal project.
3. Get the trial number. On the Console home page, click **Get a phone number** (or go to
   **Phone Numbers → Manage → Buy a number**) and pick one with Voice. It's paid from the free trial
   credit. Don't upgrade the account.
4. If the user doesn't live in the US, warn them before step 7 that calling a US number from their
   phone may cost international rates. Ask whether that's OK. A local number may be possible, but
   some countries ask for documents.
5. From the Console home page, save the Account SID and Auth Token:
   ```
   <python> setup_helper.py set TWILIO_ACCOUNT_SID <AC...>
   <python> setup_helper.py set TWILIO_AUTH_TOKEN <token>
   ```
   The agent finds the phone number by itself when there's only one.

## Step 6: ngrok (free)

ngrok gives the user's computer a public https address, so Twilio can reach the agent.

1. Open <https://dashboard.ngrok.com/signup>. The user signs up (Google or GitHub sign-in is fine).
2. Ask first, then install ngrok: `winget install -e --id ngrok.ngrok` on Windows, or
   `brew install ngrok` on Mac. If that doesn't work, download it from <https://ngrok.com/download>
   and put the `ngrok.exe` (Windows) or `ngrok` (Mac) file in the `voice-agent` folder.
3. Copy the authtoken from <https://dashboard.ngrok.com/get-started/your-authtoken>, then run
   `ngrok config add-authtoken <token>`. If ngrok is in the folder, run `.\ngrok.exe` or `./ngrok` instead.

## Step 7: Start the agent and make a test call

1. Start the agent in a window that stays open: double-click `start.bat` on Windows, or run
   `sh start.command` in the user's Terminal on Mac. It starts ngrok, saves the public address,
   points the Twilio number at the agent, and prints
   `Your agent is running. Call +1... to talk to it.`
2. In a second window, run `<python> setup_helper.py check`. Everything except **Call summaries**
   should say OK. Fix anything else it lists before going on.
3. Ask the user to call the number from the phone they verified. They'll hear Twilio's trial
   message, press any key, then talk to the agent. The agent's window shows `Incoming call`.

## Step 8: Call summaries on Telegram (free)

1. Ask the user to open Telegram, message **@BotFather**, send `/newbot`, and choose a name and a
   username ending in `bot`. BotFather replies with a token.
2. Run `<python> setup_helper.py set TELEGRAM_BOT_TOKEN <token>`.
3. Ask the user to open their new bot in Telegram and send it any message.
4. Run `<python> setup_helper.py telegram-chat-id`, then `<python> setup_helper.py test-notify`.
   The user should receive a test message.
5. Restart the agent so it uses the new settings: Ctrl+C in its window, then start it again.

If the user doesn't use Telegram, set up Gmail instead (README.md, Part 3).

## Step 9: Finish

Tell the user, briefly:

- **Starting it again later:** double-click `start.bat` (Windows), or run `sh start.command` (Mac),
  in the `voice-agent` folder. The agent only answers while that window is open and the computer is on.
- **Trial limits:** only verified numbers can call, callers hear a trial message and press a key,
  and calls end after 10 minutes. They can verify more numbers in the Twilio Console under
  **Phone Numbers → Manage → Verified Caller IDs**.
- **Going live** (anyone can call, with missed calls forwarded from their phone) is paid: they upgrade
  Twilio, which costs about $1–2 a month for a US number plus roughly 10–15 cents for a two-minute call.
  They also dial a call-forwarding code on their phone (README.md, Part 4). Don't set this up unless
  they ask.

## If something goes wrong

- `<python> setup_helper.py check` says what's wrong and how to fix it.
- **"An application error has occurred" on the call:** look at the agent's window, then restart the
  agent. That reconnects ngrok and Twilio.
- **Twilio rejects the call:** the calling phone isn't verified in the trial.
- **ngrok says authentication failed:** redo step 6.3. If it says only one session is allowed,
  close any other ngrok windows.
- **Groq says the limit is reached:** that's the free tier. Wait a minute.
- More fixes are in README.md, under "Troubleshooting".
