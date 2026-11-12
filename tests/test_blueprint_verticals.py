"""Task 2.10 — ported vertical blueprints: structural integrity tests.

Each baked vertical (insurance-claims, real-estate, trading) is a real product
pattern ported from the shipped onboard-*.sh scripts. These tests pin:
  - every declared role has a soul file that exists and is non-trivial
  - every cron references a role that exists in team.yaml
  - cron names are unique and prefixed with the team name
  - mesh edges reference only known roles
  - kanban parses with columns + tasks
"""
import json
from pathlib import Path

import pytest
import yaml

from teamops.build import Blueprint

BLUEPRINTS = Path(__file__).resolve().parent.parent / "teamops" / "blueprints"
PORTED = ["insurance-claims", "real-estate", "trading"]


@pytest.fixture(scope="module")
def ported_blueprints():
    out = {}
    for name in PORTED:
        bp = Blueprint(BLUEPRINTS / name)
        assert bp.name == name
        out[name] = bp
    return out


def test_ported_blueprints_have_full_rosters(ported_blueprints):
    # Real vertical sizes from the shipped onboard scripts:
    # insurance = 8 core + monitor + 4 satellites = 13
    # real-estate = large tier = 14
    # trading = 7 core + 3 satellites = 10
    assert len(ported_blueprints["insurance-claims"].roles) == 13
    assert len(ported_blueprints["real-estate"].roles) == 14
    assert len(ported_blueprints["trading"].roles) == 10


def test_every_role_has_existing_soul(ported_blueprints):
    for name, bp in ported_blueprints.items():
        for role in bp.roles:
            soul = bp.soul_path(role)
            assert soul is not None, f"{name}/{role.get('name')} missing soul path"
            assert soul.exists(), f"{name}/{role.get('name')} soul file missing: {soul}"
            assert soul.stat().st_size > 400, f"{name}/{role.get('name')} soul too small"


def test_crons_reference_known_roles(ported_blueprints):
    for name, bp in ported_blueprints.items():
        roles = {r["name"] for r in bp.roles}
        for cron in bp.crons:
            assert cron["profile"] in roles, \
                f"{name} cron {cron.get('name')} references unknown role {cron.get('profile')}"


def test_cron_names_unique_and_prefixed(ported_blueprints):
    for name, bp in ported_blueprints.items():
        seen = set()
        for cron in bp.crons:
            assert cron["name"].startswith(f"{name}-"), \
                f"{name} cron unprefixed: {cron['name']}"
            assert cron["name"] not in seen, f"{name} duplicate cron {cron['name']}"
            seen.add(cron["name"])


def test_mesh_edges_reference_known_roles(ported_blueprints):
    for name, bp in ported_blueprints.items():
        mesh = json.loads((bp.root / "assets" / "mesh.json").read_text())
        roles = {r["name"] for r in bp.roles}
        assert mesh.get("edges"), f"{name} mesh has no edges"
        for e in mesh["edges"]:
            assert e["from"] in roles, f"{name} mesh from {e['from']} not a role"
            assert e["to"] in roles, f"{name} mesh to {e['to']} not a role"


def test_kanban_parses(ported_blueprints):
    for name, bp in ported_blueprints.items():
        kanban = json.loads((bp.root / "assets" / "kanban.json").read_text())
        assert kanban["columns"], f"{name} kanban has no columns"
        assert kanban["tasks"], f"{name} kanban has no tasks"
        for task in kanban["tasks"].values():
            assert task["assignee"] in {r["name"] for r in bp.roles}, \
                f"{name} kanban assignee {task['assignee']} not a role"


def test_blueprint_loader_rejects_escape(tmp_path):
    """Round 2 #4: bare-name blueprint paths resolve ONLY inside the root."""
    from teamops import build
    root = tmp_path / "blueprints"
    root.mkdir()
    (root / "ok").mkdir()
    (root / "ok" / "team.yaml").write_text("name: ok\nroles: []\n")
    bp = build.load_blueprint("ok", blueprints_root=root)
    assert bp.name == "ok"
    with pytest.raises(ValueError, match="escapes allowed root"):
        build.load_blueprint("../outside", blueprints_root=root)
    with pytest.raises(ValueError, match="escapes allowed root"):
        build.load_blueprint("/etc/passwd", blueprints_root=root)


def test_trading_blueprint_paper_only():
    """Round 2 #7: the trading blueprint ships analysis/paper-only — no
    unattended money-move crons."""
    trading = BLUEPRINTS / "trading"
    team = yaml.safe_load((trading / "team.yaml").read_text())
    assert team.get("paper_only") is True
    assert "paper-only" in (team.get("description") or "").lower()
    text = (trading / "crons.yaml").read_text()
    assert "Execute\n    whitelisted" not in text            # sniper executes -> paper
    assert "Execute rebalances" not in text                  # yield farmer moves -> paper
    assert "Cancel any unfilled orders past TTL" not in text  # executor sweep -> report
    low = text.lower()
    assert ("paper-only" in low or "paper-execute" in low
            or "paper-rebalance" in low)
