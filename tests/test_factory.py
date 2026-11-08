"""Factory tests (D1): fixture fallback, handoff parsing, mock lane runs."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from teamops import factory

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "teamops" / "blueprints" / "fixtures"


def test_generate_blueprint_fixture_when_script_missing(monkeypatch):
    monkeypatch.setattr(factory, "FACTORY_SCRIPT", Path("/nonexistent/agentic-coding-team.sh"))
    root, meta = factory.generate_blueprint("add a billing agent to sales", name="billing-agent")
    assert meta["source"] == "fixture"
    assert (root / "team.yaml").exists()


def test_generate_blueprint_fallback_when_lane_fails(monkeypatch):
    def fake_run_lane(spec, timeout=600):
        return 1, "lane blew up", None

    monkeypatch.setattr(factory, "_run_lane", fake_run_lane)
    root, meta = factory.generate_blueprint("add a billing agent to sales", name="billing-agent")
    assert meta["source"] == "fixture"
    assert "fixture fallback" in meta.get("note", "")


def test_generate_blueprint_fallback_when_no_handoff(monkeypatch, tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "task.md").write_text("spec")

    def fake_run_lane(spec, timeout=600):
        return 0, "done — workspace: " + str(ws), ws

    monkeypatch.setattr(factory, "_run_lane", fake_run_lane)
    root, meta = factory.generate_blueprint("add a billing agent to sales", name="billing-agent")
    assert meta["source"] == "fixture"
    assert "no Validator handoff" in meta.get("note", "")


def test_handoff_parse_and_materialize(tmp_path):
    handoff_dir = tmp_path / "handoffs"
    handoff_dir.mkdir()
    handoff = handoff_dir / "onboard-script.json"
    handoff.write_text(json.dumps({
        "summary": "Added a billing agent to the sales team.",
        "blueprint": {
            "team.yaml": {
                "name": "sales-plus-billing",
                "version": "0.1.0",
                "roles": [
                    {"name": "boss", "title": "Boss"},
                    {"name": "billing-agent", "title": "Billing"},
                ],
            },
            "souls": {
                "boss": "# Boss soul",
                "billing-agent": "# Billing soul",
            },
            "assets": {
                "kanban.json": {"columns": ["TODO"]},
            },
        },
        "files_created": ["team.yaml", "souls/boss.md", "souls/billing-agent.md"],
        "check_results": [{"check": "yaml parses", "passed": True}],
    }))
    parsed = factory._parse_handoff(handoff)
    target = tmp_path / "out"
    factory.blueprint_from_handoff(parsed, "sales-plus-billing", target)
    assert (target / "team.yaml").exists()
    assert (target / "souls" / "billing-agent.md").exists()
    assert (target / "assets" / "kanban.json").exists()
    text = (target / "team.yaml").read_text()
    assert "sales-plus-billing" in text
    assert "souls/billing-agent.md" in text
