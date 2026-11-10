"""Optional Twilio phone intake — inbound call/SMS → structured card.

Task 2.12 (OPTIONAL, mock-first). Wires the Twilio inbound path
(webhook payload → transcribe → intent triage → structured card → agent
follow-up) without ever requiring credentials for tests or demos:

  * no Twilio creds configured  -> the LIVE path refuses cleanly
    ("disabled — Twilio credentials not configured"); the plugin stays 100%
    functional, zero nags, zero broken UI (acceptance rule).
  * mock=True / mock payloads   -> the full pipeline runs with fake
    transcripts, ledger writes and TwiML acks — always works.

Credentials follow the CREDS-SOURCE rule (boss-live 2026-11-09): the plugin
NEVER stores or ships credentials. It only READS TWILIO_ACCOUNT_SID /
TWILIO_AUTH_TOKEN / TWILIO_PHONE_NUMBER from the RUNNING USER's environment
or from <home>/config/twilio.env (the user's own dotenv file). Outputs only
ever show masked values. This module never calls the Twilio REST API — the
handler pipeline is the inbound wiring (an external webhook wrapper calls
phone_intake / handle_inbound once a real host is configured), so REAL calls
are blocked by design until the user's own creds exist.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

ENV_SID = "TWILIO_ACCOUNT_SID"
ENV_AUTH = "TWILIO_AUTH_TOKEN"
ENV_PHONE = "TWILIO_PHONE_NUMBER"
ENV_NAMES = (ENV_SID, ENV_AUTH, ENV_PHONE)

DEFAULT_HOME = Path(os.environ.get("HERMES_HOME", os.path.expanduser("~/.hermes")))

# Mock transcripts (tests/demo only — never sent anywhere).
MOCK_SMS_BODY = ("There's a water leak in the hallway and a tenant is upset, "
                 "please send someone today.")
MOCK_CALL_TRANSCRIPT = ("Hi, this is Maria from unit 3B. The bathroom sink has "
                        "been leaking since yesterday and it's dripping into the "
                        "unit below. Can someone come fix it today?")

# Intent triage rules: (keywords, label, urgency, suggested_role, note).
# Suggested roles align with the property-mgmt blueprint roster; "support" is
# the generic fallback. Keyword matching is deliberately simple — this is a
# triage card generator, not an NLU model.
TRIAGE_RULES = [
    (("emergency", "fire", "flood", "gas", "break-in", "unsafe", "injured",
      "burst"), "emergency", "high", "owner", "route immediately"),
    (("billing", "payment", "invoice", "refund", "charge", "rent"),
     "billing", "normal", "books", "collect account + amount"),
    (("maintenance", "repair", "broken", "not working", "fix", "plumbing",
      "hvac", "electrical", "sink", "leak", "leaking", "water"),
     "maintenance", "normal", "maintenance-coordinator", "create work order"),
    (("tenant", "lease", "move out", "move in", "evict", "renter"),
     "tenant", "normal", "owner", "review lease terms"),
    (("hello", "help", "question", "info", "hours", "hi"),
     "inquiry", "normal", "support", "answer or redirect"),
]


def _dotenv_values(path: Path) -> dict:
    """Parse KEY=VALUE lines (comments/blank ignored) — zero dependencies."""
    out: dict[str, str] = {}
    if not path.is_file():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        out[key.strip()] = val.strip().strip('"').strip("'")
    return out


def load_config(home: str | Path | None = None,
                environ: dict | None = None) -> dict:
    """Read Twilio creds from the running user's env / <home>/config/twilio.env.

    Returns a dict with internal (unmasked) values plus `present`/`reason`.
    Nothing is written or cached — the plugin never stores credentials.
    """
    env = dict(environ) if environ is not None else dict(os.environ)
    home_p = Path(home) if home else DEFAULT_HOME
    vals = _dotenv_values(home_p / "config" / "twilio.env")
    for name in ENV_NAMES:  # env wins over dotenv
        if name in env and env[name]:
            vals[name] = env[name]
    sid = vals.get(ENV_SID, "").strip()
    auth = vals.get(ENV_AUTH, "").strip()
    phone = vals.get(ENV_PHONE, "").strip()
    missing = [n for n, v in ((ENV_SID, sid), (ENV_AUTH, auth), (ENV_PHONE, phone)) if not v]
    present = not missing
    source = "env"
    if present and ENV_SID not in env:
        source = "twilio.env"
    reason = ""
    if missing:
        reason = (
            f"disabled — Twilio credentials not configured (missing {', '.join(missing)}). "
            f"Set {ENV_SID}/{ENV_AUTH}/{ENV_PHONE} in your environment or write "
            f"<home>/config/twilio.env. Mock mode still works (mock=True)."
        )
    return {
        "present": present,
        "account_sid": sid,   # internal only — never printed
        "auth_token": auth,   # internal only — never printed
        "phone_number": phone,
        "source": source if present else None,
        "reason": reason,
    }


def _mask(value: str) -> str:
    if len(value) <= 4:
        return "***"
    return value[:2] + "…" + value[-4:]


@dataclass
class Inbound:
    """Normalized inbound message (raw Twilio webhook fields friendly-ified)."""
    channel: str            # 'sms' | 'call'
    sid: str
    from_number: str
    to_number: str
    body: str = ""
    call_status: str = ""
    digits: str = ""


def parse_payload(data: dict | None) -> Inbound:
    """Normalize a Twilio webhook form payload OR our convenience fields.

    Raw Twilio: MessageSid/CallSid, From, To, Body (SMS) or CallStatus/Digits
    (voice). Convenience keys (`channel`, `sid`, `from_number`, `to_number`,
    `body`, `call_status`) win when present.
    """
    data = {k: v for k, v in (data or {}).items() if v is not None}
    # Ambiguous/empty payloads default to sms; a raw call payload has a
    # CallSid and no Body/MessageSid.
    if data.get("channel"):
        channel = data["channel"]
    elif data.get("CallSid") and not data.get("Body") and not data.get("MessageSid"):
        channel = "call"
    else:
        channel = "sms"
    sid = data.get("sid") or data.get("MessageSid") or data.get("CallSid") or "mock"
    from_number = data.get("from_number") or data.get("From") or ""
    to_number = data.get("to_number") or data.get("To") or ""
    body = (data.get("body") or data.get("Body") or "").strip()
    call_status = data.get("call_status") or data.get("CallStatus") or ""
    digits = str(data.get("Digits") or "")
    return Inbound(channel=channel, sid=str(sid), from_number=str(from_number),
                   to_number=str(to_number), body=str(body),
                   call_status=str(call_status), digits=digits)


def triage(text: str) -> dict:
    """Keyword intent triage -> {intent, urgency, suggested_role, note, confidence}."""
    low = (text or "").lower()
    for keywords, label, urgency, role, note in TRIAGE_RULES:
        if any(k in low for k in keywords):
            return {"intent": label, "urgency": urgency,
                    "suggested_role": role, "note": note, "confidence": "high"}
    return {"intent": "inquiry", "urgency": "normal",
            "suggested_role": "support", "note": "answer or redirect",
            "confidence": "low"}


def mock_transcript(channel: str = "call") -> str:
    return MOCK_CALL_TRANSCRIPT if channel == "call" else MOCK_SMS_BODY


def build_card(inb: Inbound, t: dict, mode: str,
               transcript: str | None = None,
               needs_transcription: bool = False) -> dict:
    return {
        "id": f"{inb.channel}-{inb.sid}",
        "channel": inb.channel,
        "from_number": inb.from_number,
        "to_number": inb.to_number,
        "received_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "body": inb.body or None,
        "transcript": transcript,
        "needs_transcription": needs_transcription,
        "intent": t["intent"],
        "urgency": t["urgency"],
        "suggested_role": t["suggested_role"],
        "note": t["note"],
        "mode": mode,
        "next_action": f"hand to {t['suggested_role']} — {t['note']}",
    }


def write_ledger(card: dict, home: str | Path | None = None) -> Path:
    """Persist the structured card as the agent follow-up inbox entry."""
    home_p = Path(home) if home else DEFAULT_HOME
    d = home_p / "plugin-data" / "teamops" / "intake"
    d.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    slug = str(card.get("id") or "anon").replace("/", "-").replace(":", "-")[:60]
    p = d / f"{ts}-{slug}.json"
    p.write_text(json.dumps(card, indent=2, ensure_ascii=False))
    return p


def twiml_ack(inb: Inbound, text: str = "") -> str:
    """TwiML reply a webhook wrapper can return to Twilio."""
    if inb.channel == "sms":
        ack = "Thanks — your message has been received and routed to the team."
        return (f'<?xml version="1.0" encoding="UTF-8"?>'
                f'<Response><Message>{ack}</Message></Response>')
    return (
        '<?xml version="1.0" encoding="UTF-8"?><Response>'
        '<Say voice="alice">Thanks for calling — please leave a message after '
        'the tone and we will get back to you.</Say>'
        '<Record maxLength="30" action="/intake/recording" /></Response>'
    )


def handle_inbound(data: dict | None, home: str | Path | None = None,
                   mock: bool = False, transcript: str | None = None,
                   persist: bool = True) -> dict:
    """Full inbound pipeline: parse → transcribe → triage → card → follow-up.

    Live mode requires the user's own Twilio creds (see load_config); without
    them (and without mock=True) this returns a clean disabled refusal and
    writes nothing. Mock mode always runs the pipeline.
    """
    cfg = load_config(home)
    if not cfg["present"] and not mock:
        return {
            "success": False, "mode": "disabled", "enabled": False,
            "error": cfg["reason"],
            "hint": "run with mock=True for a demo, or export the Twilio "
                    "env vars / write <home>/config/twilio.env",
        }
    inb = parse_payload(data)
    transcript = transcript or (data or {}).get("transcript") or None
    mode = "mock" if mock else "live"
    needs_transcription = False
    text = inb.body
    if inb.channel == "call":
        if transcript:
            text = transcript
        elif mock:
            transcript = mock_transcript("call")
            text = transcript
        else:
            needs_transcription = True  # real voice needs STT wiring
            text = inb.digits or ""
    # Real call with no transcript and no digits: no text to triage yet.
    if inb.channel == "call" and needs_transcription and not text:
        t = triage("")
    else:
        t = triage(text)
    card = build_card(inb, t, mode, transcript=transcript,
                      needs_transcription=needs_transcription)
    ledger = None
    if persist:
        ledger = write_ledger(card, home)
    return {
        "success": True, "mode": mode, "enabled": cfg["present"],
        "card": card, "ledger": str(ledger) if ledger else None,
        "twiml": twiml_ack(inb, transcript or inb.body),
    }


def status(home: str | Path | None = None) -> dict:
    """Feature status for twilio_status / CLI — secrets masked, zero nags."""
    cfg = load_config(home)
    return {
        "enabled": cfg["present"],
        "mode": "live" if cfg["present"] else "mock-only",
        "account_sid_masked": _mask(cfg["account_sid"]) if cfg["account_sid"] else None,
        "phone_number_masked": _mask(cfg["phone_number"]) if cfg["phone_number"] else None,
        "source": cfg["source"],
        "reason": cfg["reason"] or "Twilio intake enabled — real inbound calls/SMS route through phone_intake",
        "how_to_enable": (f"export {ENV_SID}=... {ENV_AUTH}=... {ENV_PHONE}=... "
                          f"(or write <home>/config/twilio.env)"),
    }


def mock_sample(channel: str = "sms") -> dict:
    """A ready-made inbound payload for demos/tests (never sent anywhere)."""
    if channel == "sms":
        return {"channel": "sms", "MessageSid": "SMmock1234",
                "From": "+15551234567", "To": "+15559876543",
                "Body": MOCK_SMS_BODY}
    return {"channel": "call", "CallSid": "CAmock1234",
            "From": "+15551234567", "To": "+15559876543",
            "CallStatus": "in-progress"}
