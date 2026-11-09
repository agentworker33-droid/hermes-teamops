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
    # isolate from the real harness workspace on this box (Task 2.7 live run
    # left a valid handoff there — the disk fallback would find it)
    monkeypatch.setattr(factory, "_team_workspace_roots", lambda: [])

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


def test_extract_workspace_with_spaces(tmp_path):
    """Regression: task ids keep spaces ('3-agent lawn-care ops team-…'), the
    old token-split extraction missed them and fell back to an empty tmp dir."""
    ws = tmp_path / "3-agent lawn-care ops team-20261108_213020"
    ws.mkdir()
    out = f"\x1b[34m[agentic-team]\x1b[0m task workspace: {ws}\n[OK] Done. Workspace: {ws}\n"
    assert factory._extract_workspace(out) == ws


def test_extract_workspace_returns_none_when_missing(tmp_path):
    assert factory._extract_workspace("no workspace marker here\n") is None
    assert factory._extract_workspace(f"task workspace: {tmp_path / 'nope'}") is None


def test_find_handoff_fallback_team_workspace(tmp_path, monkeypatch):
    """Regression: when output parsing misses the workspace (e.g. spaces), the
    disk search under ~/.hermes/ng-team/workspace still locates the handoff."""
    ws = tmp_path / "ng-team" / "workspace" / "3-agent lawn-care ops team-20261108_213020" / "handoffs"
    ws.mkdir(parents=True)
    handoff = ws / "onboard-script.json"
    handoff.write_text('{"status": "complete"}')
    monkeypatch.setattr(factory, "_team_workspace_roots", lambda: [tmp_path / "ng-team" / "workspace"])
    found = factory._find_handoff(None)
    assert found is not None and found.name == "onboard-script.json"


def test_blueprint_from_artifacts_real_schema(tmp_path):
    """The agent-factory lane's real onboarding-script.json lists artifact paths
    on disk (no embedded team.yaml). blueprint_from_handoff must convert that
    into a deployable blueprint: team.yaml, crons.yaml, souls, mesh, kanban."""
    arts = tmp_path / "artifacts"
    arts.mkdir()
    (arts / "MESH-MAP.md").write_text(
        "# LawnCare Co - MESH-MAP (3-agent lawn-care ops team)\n\n"
        "Slug: lawncare-co\n"
        "Roster: R1 dispatcher, R2 field-agent, R3 boss\n"
        "Plan: $350/mo\n\n"
        "## Edges\n\n"
        "| E1 | dispatcher -> field-agent | Daily dispatch: write assignments into INBOX-field-agent.md |\n"
        "| E2 | field-agent -> boss | Weekly inspection: write ops report into INBOX-boss.md |\n"
        "| E3 | boss -> dispatcher | Morning brief: write priorities into INBOX-dispatcher.md |\n")
    (arts / "README.md").write_text(
        "# LawnCare Co\n\n## Roster\n\n| R# | Agent | Role |\n| --- | --- | --- |\n"
        "| R1 | dispatcher | Daily dispatch / work-order writer |\n"
        "| R2 | field-agent | Weekly inspection / ops reports |\n"
        "| R3 | boss | Morning brief / oversight |\n\n## Cron jobs\n\n"
        "| Cron name | Schedule | Agent |\n| --- | --- | --- |\n"
        "| lawncare-co-dispatcher-daily-dispatch | 0 8 * * * | dispatcher |\n"
        "| lawncare-co-field-agent-weekly-inspection | 0 9 * * 6 | field-agent |\n"
        "| lawncare-co-boss-morning-brief | 0 7 * * * | boss |\n")
    for role, body in [
        ("dispatcher", "Daily dispatch agent."),
        ("field-agent", "Weekly inspection agent."),
        ("boss", "Morning brief agent."),
    ]:
        (arts / f"INBOX-{role}.md").write_text(f"# INBOX - {role}\n\n{body}\n")
    (arts / "onboard-lawn-care.sh").write_text("#!/bin/bash\necho hi\n")
    (arts / "verify-lawn-care.sh").write_text("#!/bin/bash\necho ok\n")

    files_created = [str(p) for p in sorted(arts.iterdir())]
    parsed = {
        "summary": "Implemented the 3-agent lawn-care ops team.",
        "blueprint": {},
        "files_created": files_created,
        "checks": [{
            "check": "dry-run smoke",
            "passed": True,
            "output": ("DRY_RUN_CRON lawncare-co-dispatcher-daily-dispatch|0 8 * * *|dispatcher\n"
                        "DRY_RUN_CRON lawncare-co-field-agent-weekly-inspection|0 9 * * 6|field-agent\n"
                        "DRY_RUN_CRON lawncare-co-boss-morning-brief|0 7 * * *|boss\n"),
        }],
    }
    target = tmp_path / "out"
    factory.blueprint_from_handoff(parsed, "lawn-care-ops", target)

    team = (target / "team.yaml").read_text()
    assert "lawn-care-ops" in team
    assert "dispatcher" in team and "field-agent" in team and "boss" in team
    crons = (target / "crons.yaml").read_text()
    assert "0 8 * * *" in crons and "0 9 * * 6" in crons and "0 7 * * *" in crons
    mesh = json.loads((target / "assets" / "mesh.json").read_text())
    assert len(mesh["edges"]) == 3
    assert mesh["edges"][0]["from"] == "dispatcher"
    # drop-file contract: source writes into the DESTINATION's INBOX
    assert mesh["edges"][0]["artifact"] == "INBOX-field-agent.md"
    assert mesh["edges"][1]["artifact"] == "INBOX-boss.md"
    assert mesh["edges"][2]["artifact"] == "INBOX-dispatcher.md"
    assert (target / "souls" / "dispatcher.md").exists()
    assert (target / "souls" / "boss.md").exists()
    assert (target / "assets" / "kanban.json").exists()
    assert (target / "assets" / "generated" / "onboard-lawn-care.sh").exists()
