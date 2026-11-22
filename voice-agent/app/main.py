"""Web server that Twilio calls when someone phones your agent's number.

Call flow:
  Twilio  -> POST /voice/incoming   greet the caller and listen
  Twilio  -> POST /voice/respond    caller spoke: the AI replies, listen again (repeat)
  Twilio  -> POST /voice/status     call ended: summarize and notify you
  You     -> GET  /calls            call log dashboard (password protected)
"""

from __future__ import annotations

import asyncio
import html
import logging
import secrets

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import HTMLResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from twilio.request_validator import RequestValidator
from twilio.twiml.voice_response import VoiceResponse

from .brain import Brain, Conversation, make_brain
from .config import Settings
from .notify import Notifier
from .store import CallStore

log = logging.getLogger("voice_agent")

MAX_SILENT_PROMPTS = 2


def create_app(
    settings: Settings | None = None,
    brain: Brain | None = None,
    notifier: Notifier | None = None,
    store: CallStore | None = None,
) -> FastAPI:
    settings = settings or Settings.from_env()
    brain = brain or make_brain(settings)
    notifier = notifier or Notifier(settings)
    store = store or CallStore(settings.database_path)
    validator = RequestValidator(settings.twilio_auth_token) if settings.twilio_auth_token else None

    live: dict[str, Conversation] = {}
    silent_prompts: dict[str, int] = {}

    app = FastAPI(title="Voice agent")
    app.state.live = live
    app.state.store = store

    # ---------- helpers ----------

    async def twilio_form(request: Request) -> dict[str, str]:
        form = {k: str(v) for k, v in (await request.form()).items()}
        if not settings.validate_twilio_signature:
            return form
        if validator is None:
            log.error("TWILIO_AUTH_TOKEN is not set, so requests cannot be verified")
            raise HTTPException(status_code=403)
        if settings.public_base_url:
            url = settings.public_base_url + request.url.path
            if request.url.query:
                url += "?" + request.url.query
        else:
            url = str(request.url)
        if not validator.validate(url, form, request.headers.get("X-Twilio-Signature", "")):
            log.warning("Rejected a request with a bad Twilio signature (url=%s)", url)
            raise HTTPException(status_code=403)
        return form

    def twiml(vr: VoiceResponse) -> Response:
        return Response(content=str(vr), media_type="application/xml")

    def say(target, text: str) -> None:
        if settings.tts_voice.lower() in {"man", "woman", "alice"}:
            target.say(text, voice=settings.tts_voice, language=settings.call_language)
        else:  # Polly/Google voices already speak one language; don't risk a mismatch
            target.say(text, voice=settings.tts_voice)

    def say_and_listen(text: str) -> Response:
        vr = VoiceResponse()
        gather = vr.gather(
            input="speech",
            action="/voice/respond",
            method="POST",
            speech_timeout="auto",
            language=settings.call_language,
        )
        say(gather, text)  # inside <Gather> so the caller can talk over it
        vr.redirect("/voice/no-input", method="POST")  # reached only if they said nothing
        return twiml(vr)

    def say_and_hang_up(text: str) -> Response:
        vr = VoiceResponse()
        say(vr, text)
        vr.hangup()
        return twiml(vr)

    async def finalize(sid: str) -> None:
        if not store.claim_for_finalize(sid):
            return  # already done (both /status and our own hang-up can trigger this)
        live.pop(sid, None)
        silent_prompts.pop(sid, None)
        call = store.get(sid)
        if call is None:
            return
        summary = None
        try:
            result = await brain.summarize(call["transcript"], call["from_number"] or "unknown")
            summary = result.model_dump() if result is not None else None
        except Exception:
            log.exception("Could not summarize call %s; sending the raw transcript instead", sid)
        store.save_summary(sid, summary)
        call["summary"] = summary
        await asyncio.to_thread(notifier.send, call)

    # ---------- Twilio webhooks ----------

    @app.post("/voice/incoming")
    async def incoming(request: Request, background: BackgroundTasks) -> Response:
        form = await twilio_form(request)
        sid = form.get("CallSid", "")
        caller = form.get("From", "")
        store.start_call(sid, caller)
        live[sid] = Conversation()
        greeting = brain.greeting()
        store.add_line(sid, "agent", greeting)
        log.info("Incoming call %s from %s", sid, caller)
        warm_up = getattr(brain, "warm_up", None)
        if warm_up is not None:
            background.add_task(warm_up)  # loads a local model while the greeting plays
        return say_and_listen(greeting)

    @app.post("/voice/respond")
    async def respond(request: Request, background: BackgroundTasks) -> Response:
        form = await twilio_form(request)
        sid = form.get("CallSid", "")
        speech = form.get("SpeechResult", "").strip()
        if not speech:
            return await no_input_reply(sid, background)

        silent_prompts.pop(sid, None)
        if sid not in live:  # e.g. the server restarted mid-call
            store.start_call(sid, form.get("From", ""))
            live[sid] = Conversation()
        store.add_line(sid, "caller", speech)

        try:
            reply = await asyncio.wait_for(
                brain.reply(live[sid], speech), timeout=settings.llm_timeout_seconds + 1
            )
        except asyncio.TimeoutError:
            log.warning("The AI took too long to reply on call %s", sid)
            return say_and_listen("Sorry, I missed that. Could you say it again?")
        except Exception:  # never drop the call because the AI failed
            log.exception("The AI failed to reply on call %s", sid)
            return say_and_listen("Sorry, I'm having trouble hearing you. Could you repeat that?")

        store.add_line(sid, "agent", reply.text)
        if reply.end_call:
            background.add_task(finalize, sid)
            return say_and_hang_up(reply.text)
        return say_and_listen(reply.text)

    async def no_input_reply(sid: str, background: BackgroundTasks) -> Response:
        count = silent_prompts.get(sid, 0) + 1
        silent_prompts[sid] = count
        if count > MAX_SILENT_PROMPTS:
            text = f"I can't hear anything, so I'll end the call. I'll let {settings.owner_name} know you called. Goodbye."
            store.add_line(sid, "agent", text)
            background.add_task(finalize, sid)
            return say_and_hang_up(text)
        return say_and_listen("Sorry, I didn't catch that. Who's calling, and what can I pass on?")

    @app.post("/voice/no-input")
    async def no_input(request: Request, background: BackgroundTasks) -> Response:
        form = await twilio_form(request)
        return await no_input_reply(form.get("CallSid", ""), background)

    @app.post("/voice/status")
    async def status(request: Request, background: BackgroundTasks) -> Response:
        form = await twilio_form(request)
        if form.get("CallStatus") in {"completed", "busy", "failed", "no-answer", "canceled"}:
            background.add_task(finalize, form.get("CallSid", ""))
        return Response(status_code=204)

    # ---------- dashboard ----------

    basic = HTTPBasic(auto_error=False)

    def check_password(creds: HTTPBasicCredentials | None = Depends(basic)) -> None:
        if not settings.dashboard_password:
            raise HTTPException(status_code=404)  # dashboard is off until a password is set
        if creds is None or not secrets.compare_digest(
            creds.password.encode(), settings.dashboard_password.encode()
        ):
            raise HTTPException(status_code=401, headers={"WWW-Authenticate": "Basic"})

    @app.get("/calls", response_class=HTMLResponse, dependencies=[Depends(check_password)])
    async def calls_page() -> str:
        return render_dashboard(store.recent(), settings)

    @app.get("/health")
    async def health() -> dict:
        return {
            "ok": True,
            "brain": getattr(brain, "label", type(brain).__name__),
            "notify": notifier.channels(),
        }

    return app


def render_dashboard(calls: list[dict], settings: Settings) -> str:
    e = html.escape
    cards = []
    for call in calls:
        summary = call["summary"] or {}
        urgent = summary.get("urgency") == "high"
        lines = "".join(
            f'<p class="{e(t["role"])}"><b>{"Caller" if t["role"] == "caller" else e(settings.agent_name)}:</b> {e(t["text"])}</p>'
            for t in call["transcript"]
        )
        head = e(summary.get("caller_name", "")) or "Caller"
        cards.append(
            f"""<article class="{'urgent' if urgent else ''}">
  <header><strong>{head}</strong> · {e(call['from_number'] or 'unknown')}
  <span class="when">{e(call['started_at'] or '')} UTC</span>{' <span class="tag">URGENT</span>' if urgent else ''}</header>
  {f"<p class='msg'>{e(summary.get('message', ''))}</p>" if summary else ''}
  {f"<p class='cb'>Call back: {e(summary.get('best_time_to_call_back', ''))}</p>" if summary.get('callback_requested') else ''}
  <details><summary>Transcript</summary>{lines}</details>
</article>"""
        )
    body = "\n".join(cards) or "<p>No calls yet.</p>"
    return f"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Call log</title>
<style>
:root {{ --bg:#f6f7f9; --card:#fff; --text:#1d2330; --muted:#667085; --accent:#d92d20; }}
@media (prefers-color-scheme: dark) {{ :root {{ --bg:#111318; --card:#1b1e25; --text:#e6e8ec; --muted:#9aa3b2; --accent:#ff6b5e; }} }}
body {{ margin:0; padding:16px; background:var(--bg); color:var(--text); font:15px/1.5 system-ui, sans-serif; }}
main {{ max-width:760px; margin:0 auto; }}
article {{ background:var(--card); border-radius:10px; padding:12px 16px; margin:12px 0; border-left:4px solid transparent; }}
article.urgent {{ border-left-color:var(--accent); }}
.when {{ color:var(--muted); font-size:13px; margin-left:6px; }}
.tag {{ color:var(--accent); font-weight:700; font-size:12px; }}
.msg {{ margin:8px 0 4px; }} .cb {{ color:var(--muted); margin:0; }}
details p {{ margin:4px 0; }} summary {{ cursor:pointer; color:var(--muted); }}
</style></head><body><main><h1>Calls answered by {e(settings.agent_name)}</h1>{body}</main></body></html>"""


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
