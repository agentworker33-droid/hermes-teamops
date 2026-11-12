"""Twilio phone intake tests (Task 2.12).

OPTIONAL, mock-first: no credentials are ever required for tests or demos.
The suite asserts the full graceful-degrade contract — absent Twilio creds →
clean disabled refusal, plugin stays 100% functional; mock=True runs the
whole pipeline (parse → transcribe → triage → card → ledger → TwiML ack).
The plugin never stores/ships credentials (catalog review round 2 #6): only
Hermes' secret scope (agent.secret_scope.get_secret) or the running user's
env is read — no twilio.env file — and outputs only contain masked values.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from teamops import schemas, tools, twilio_intake as ti

ROOT = Path(__file__).resolve().parent.parent
ENV_NAMES = (ti.ENV_SID, ti.ENV_AUTH, ti.ENV_PHONE)


@pytest.fixture(autouse=True)
def _no_twilio_env(monkeypatch):
    """Tests must never see the host's real Twilio creds."""
    for name in ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(ti, "_secret_scope_get", lambda name: None)


SCOPED = {ti.ENV_SID: "ACscoped", ti.ENV_AUTH: "tokscope", ti.ENV_PHONE: "+155****0000"}


# -- config (creds source) ----------------------------------------------------
def test_config_from_env():
    cfg = ti.load_config(environ={ti.ENV_SID: "ACabc", ti.ENV_AUTH: "tok",
                                  ti.ENV_PHONE: "+155****4567"})
    assert cfg["present"] is True
    assert cfg["source"] == "env"
    assert cfg["reason"] == ""


def test_config_from_secret_scope(tmp_path, monkeypatch):
    monkeypatch.setattr(ti, "_secret_scope_get", lambda name: SCOPED.get(name))
    cfg = ti.load_config(home=tmp_path)
    assert cfg["present"] is True
    assert cfg["source"] == "secret_scope"
    assert cfg["account_sid"] == "ACscoped"


def test_config_missing_creds(tmp_path):
    cfg = ti.load_config(home=tmp_path)
    assert cfg["present"] is False
    assert "disabled" in cfg["reason"]
    for name in ENV_NAMES:
        assert name in cfg["reason"]


def test_config_partial_creds(tmp_path):
    cfg = ti.load_config(home=tmp_path, environ={ti.ENV_SID: "AConly"})
    assert cfg["present"] is False
    assert ti.ENV_AUTH in cfg["reason"] and ti.ENV_PHONE in cfg["reason"]


def test_env_overrides_secret_scope(tmp_path, monkeypatch):
    monkeypatch.setattr(ti, "_secret_scope_get", lambda name: SCOPED.get(name))
    cfg = ti.load_config(home=tmp_path, environ={ti.ENV_SID: "ACenv"})
    assert cfg["account_sid"] == "ACenv"
    assert cfg["source"] == "env"


def test_status_masks_secrets(tmp_path, monkeypatch):
    monkeypatch.setenv(ti.ENV_SID, "AC0123456789")
    monkeypatch.setenv(ti.ENV_AUTH, "AUTHTOK999999")
    monkeypatch.setenv(ti.ENV_PHONE, "+155****4567")
    s = ti.status(home=tmp_path)
    assert s["enabled"] is True
    assert s["mode"] == "live"
    outp = json.dumps(s, ensure_ascii=False)
    assert "AC0123456789" not in outp and "AUTHTOK999999" not in outp
    assert "AC…6789" in outp and "+1…4567" in outp


def test_status_disabled_zero_nags(tmp_path):
    s = ti.status(home=tmp_path)
    assert s["enabled"] is False and s["mode"] == "mock-only"
    assert "how_to_enable" in s  # guidance, not a nag


# -- payload parsing -----------------------------------------------------------
def test_parse_payload_sms_raw():
    inb = ti.parse_payload({"MessageSid": "SMabc", "From": "+15551112222",
                            "To": "+15553334444", "Body": "need help"})
    assert inb.channel == "sms"
    assert inb.sid == "SMabc"
    assert inb.from_number == "+15551112222"
    assert inb.body == "need help"


def test_parse_payload_call_raw():
    inb = ti.parse_payload({"CallSid": "CAxyz", "From": "+15551112222",
                            "To": "+15553334444", "CallStatus": "in-progress",
                            "Digits": "2"})
    assert inb.channel == "call"
    assert inb.call_status == "in-progress"
    assert inb.digits == "2"


def test_parse_payload_convenience_keys():
    inb = ti.parse_payload({"channel": "sms", "sid": "S1", "from_number": "A",
                            "to_number": "B", "body": "hi", "call_status": ""})
    assert inb.channel == "sms" and inb.sid == "S1"
    assert inb.from_number == "A" and inb.body == "hi"


def test_parse_payload_empty_defaults():
    inb = ti.parse_payload({})
    assert inb.channel == "sms" and inb.sid == "mock"
    assert inb.from_number == "" and inb.body == ""


# -- intent triage -------------------------------------------------------------
def test_triage_emergency():
    t = ti.triage("There's a fire in the hallway, emergency!")
    assert t["intent"] == "emergency" and t["urgency"] == "high"
    assert t["suggested_role"] == "owner"


def test_triage_maintenance():
    t = ti.triage("the hvac is broken and the sink leaks")
    assert t["intent"] == "maintenance"
    assert t["suggested_role"] == "maintenance-coordinator"


def test_triage_billing():
    t = ti.triage("I never received my invoice")
    assert t["intent"] == "billing" and t["suggested_role"] == "books"


def test_triage_fallback():
    t = ti.triage("hello there")
    assert t["intent"] == "inquiry" and t["urgency"] == "normal"
    assert t["suggested_role"] == "support"
    assert t["confidence"] == "high"  # matched the inquiry rule


def test_triage_unknown_text_low_confidence():
    t = ti.triage("xyzzy qwerty plugh")
    assert t["intent"] == "inquiry" and t["confidence"] == "low"


# -- full pipeline -------------------------------------------------------------
def test_handle_inbound_mock_sms(tmp_path):
    out = ti.handle_inbound(ti.mock_sample("sms"), home=tmp_path, mock=True)
    assert out["success"] is True
    assert out["mode"] == "mock" and out["enabled"] is False
    card = out["card"]
    assert card["channel"] == "sms"
    assert card["intent"] == "maintenance"  # "water leak" keyword
    assert card["suggested_role"] == "maintenance-coordinator"
    assert card["id"] == "sms-SMmock1234"
    assert out["ledger"] and Path(out["ledger"]).is_file()


def test_handle_inbound_mock_call_uses_transcript(tmp_path):
    out = ti.handle_inbound(ti.mock_sample("call"), home=tmp_path, mock=True)
    card = out["card"]
    assert card["channel"] == "call"
    assert card["needs_transcription"] is False
    assert "Maria" in (card["transcript"] or "")
    assert card["intent"] == "maintenance"


def test_handle_inbound_custom_transcript(tmp_path):
    out = ti.handle_inbound(ti.mock_sample("call"), home=tmp_path, mock=True,
                            transcript="I want to dispute an invoice charge")
    card = out["card"]
    assert card["transcript"] == "I want to dispute an invoice charge"
    assert card["intent"] == "billing"


def test_handle_inbound_live_blocked_without_creds(tmp_path):
    out = ti.handle_inbound(ti.mock_sample("sms"), home=tmp_path, mock=False)
    assert out["success"] is False
    assert out["mode"] == "disabled"
    assert "disabled" in out["error"]
    # nothing written
    assert not (tmp_path / "plugin-data").exists()


def test_handle_inbound_live_with_creds(tmp_path, monkeypatch):
    monkeypatch.setenv(ti.ENV_SID, "AClive")
    monkeypatch.setenv(ti.ENV_AUTH, "tok")
    monkeypatch.setenv(ti.ENV_PHONE, "+15551234567")
    out = ti.handle_inbound(ti.mock_sample("sms"), home=tmp_path, mock=False)
    assert out["success"] is True
    assert out["mode"] == "live" and out["enabled"] is True
    assert out["card"]["intent"] == "maintenance"


def test_real_call_without_transcript_needs_transcription(tmp_path, monkeypatch):
    monkeypatch.setenv(ti.ENV_SID, "AClive")
    monkeypatch.setenv(ti.ENV_AUTH, "tok")
    monkeypatch.setenv(ti.ENV_PHONE, "+15551234567")
    out = ti.handle_inbound(ti.mock_sample("call"), home=tmp_path, mock=False)
    card = out["card"]
    assert card["needs_transcription"] is True
    assert card["transcript"] is None
    assert out["twiml"].startswith("<?xml")


def test_persist_false_skips_ledger(tmp_path):
    out = ti.handle_inbound(ti.mock_sample("sms"), home=tmp_path, mock=True,
                            persist=False)
    assert out["success"] is True and out["ledger"] is None
    assert not (tmp_path / "plugin-data").exists()


def test_ledger_written_json_matches_card(tmp_path):
    out = ti.handle_inbound(ti.mock_sample("sms"), home=tmp_path, mock=True)
    p = Path(out["ledger"])
    data = json.loads(p.read_text())
    assert data["id"] == out["card"]["id"]
    assert data["intent"] == "maintenance"
    assert "received_at" in data


# -- TwiML / samples -----------------------------------------------------------
def test_twiml_sms_ack():
    xml = ti.twiml_ack(ti.parse_payload({"MessageSid": "S", "Body": "hi"}))
    assert "<Response>" in xml and "<Message>" in xml
    assert "received" in xml.lower()


def test_twiml_call_ack():
    xml = ti.twiml_ack(ti.parse_payload({"CallSid": "C", "CallStatus": "ringing"}))
    assert "<Response>" in xml and "<Say" in xml


def test_mock_sample_shapes():
    sms = ti.mock_sample("sms")
    assert sms["channel"] == "sms" and "MessageSid" in sms
    call = ti.mock_sample("call")
    assert call["channel"] == "call" and "CallSid" in call


# -- tool + CLI wiring ---------------------------------------------------------
def test_tools_phone_intake_mock(tmp_path):
    out = json.loads(tools.phone_intake(mock=True, home=str(tmp_path)))
    assert out["success"] is True
    assert out["mode"] == "mock"
    assert out["card"]["channel"] == "sms"


def test_tools_phone_intake_disabled_without_creds(tmp_path):
    out = json.loads(tools.phone_intake(home=str(tmp_path)))
    assert out["success"] is False
    assert "disabled" in out["error"]


def test_tools_phone_intake_merges_convenience_fields(tmp_path):
    out = json.loads(tools.phone_intake(mock=True, home=str(tmp_path),
                                        channel="sms", from_number="+15550000000",
                                        body="Please fix the heater"))
    card = out["card"]
    assert card["from_number"] == "+15550000000"
    assert card["intent"] == "maintenance"
    assert card["id"].startswith("sms-")


def test_tools_twilio_status_masked(tmp_path, monkeypatch):
    monkeypatch.setenv(ti.ENV_SID, "ACsecret1234")
    monkeypatch.setenv(ti.ENV_AUTH, "tok")
    monkeypatch.setenv(ti.ENV_PHONE, "+15551234567")
    out = json.loads(tools.twilio_status(home=str(tmp_path)))
    assert out["success"] is True and out["enabled"] is True
    assert "ACsecret1234" not in json.dumps(out)


def test_cli_phone_intake_mock(tmp_path):
    proc = subprocess.run(
        [sys.executable, "-m", "teamops.cli", "teams", "phone-intake",
         "--mock", "--channel", "sms", "--home", str(tmp_path)],
        capture_output=True, text=True, cwd=str(ROOT), timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    assert "mock" in proc.stdout and "intent" in proc.stdout


def test_cli_twilio_status_disabled(tmp_path):
    proc = subprocess.run(
        [sys.executable, "-m", "teamops.cli", "teams", "twilio-status",
         "--home", str(tmp_path)],
        capture_output=True, text=True, cwd=str(ROOT), timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    assert "mock-only" in proc.stdout


def test_manifest_declares_twilio_tools():
    import yaml
    doc = yaml.safe_load((ROOT / "teamops" / "plugin.yaml").read_text())
    declared = set(doc["provides_tools"])
    assert "phone_intake" in declared and "twilio_status" in declared
    assert declared == set(schemas.SCHEMAS)  # full parity
    assert len(declared) == 16
