"""Factory tests (catalog review #3): the Agent Factory is template-only —
no home-dir script runner. Tests cover fixture fallback, spec parsing,
extend/scaffold generation, and blueprint-root confinement."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from teamops import factory

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "teamops" / "blueprints" / "fixtures"
SALES = ROOT / "teamops" / "blueprints" / "sales"

# The real root resolver, captured before the autouse fixture monkeypatches it.
_REAL_OWNER_ROOT = factory._owner_blueprint_root


@pytest.fixture(autouse=True)
def _isolate_blueprint_root(tmp_path, monkeypatch):
    """Factory writes go to a temp root, never the repo blueprints dir."""
    root = tmp_path / "blueprints"
    root.mkdir()
    monkeypatch.setattr(factory, "_owner_blueprint_root", lambda home=None: root)
    return root


def test_generate_blueprint_fixture_when_requested(tmp_path):
    root, meta = factory.generate_blueprint("add a billing agent to sales",
                                            name="billing-agent", use_fixture=True)
    assert meta["source"] == "fixture"
    assert (root / "team.yaml").exists()


def test_generate_blueprint_fixture_fallback_any(tmp_path):
    root, meta = factory.generate_blueprint("add a whatever agent to sales",
                                            name="no-such-fixture", use_fixture=True)
    assert meta["source"] == "fixture"


def test_generate_blueprint_raises_without_fixture_or_base(tmp_path, monkeypatch):
    monkeypatch.setattr(factory, "FIXTURES_DIR", tmp_path / "no-fixtures")
    with pytest.raises(FileNotFoundError):
        factory.generate_blueprint("add a billing agent to sales", name="billing-agent",
                                   use_fixture=True)


def test_parse_spec_add_role_to_base():
    assert factory._parse_spec("add a billing agent to sales") == ("billing-agent", "sales")
    assert factory._parse_spec("add an ops lead to the property-mgmt team") == (
        "ops-lead", "property-mgmt")
    assert factory._parse_spec("extend the sales blueprint with a support lead") == (
        "support-lead", "sales")
    assert factory._parse_spec("random words") == (None, None)


def test_roles_from_spec_scaffold():
    roles = factory._roles_from_spec("3-agent lawn-care team: dispatcher, field-agent, boss",
                                     fallback="generated")
    assert roles == ["dispatcher", "field-agent", "boss"]
    assert factory._roles_from_spec("no roles here", fallback="ops") == ["ops"]


def test_generate_blueprint_extend_base(tmp_path):
    root, meta = factory.generate_blueprint("add a billing agent to sales", name="sales-billing")
    assert meta["source"] == "extend"
    import yaml
    doc = yaml.safe_load((root / "team.yaml").read_text())
    roles = [r["name"] for r in doc["roles"]]
    assert "billing-agent" in roles
    assert (root / "souls" / "billing-agent.md").exists()
    assert (root / "crons.yaml").exists()
    mesh = json.loads((root / "assets" / "mesh.json").read_text())
    assert any(e["to"] == "billing-agent" for e in mesh["edges"])
    kanban = json.loads((root / "assets" / "kanban.json").read_text())
    assert "billing-agent" in kanban["tasks"]["seed_1"]["assignee"] or any(
        t["assignee"] == "billing-agent" for t in kanban["tasks"].values())


def test_generate_blueprint_extend_base_not_found(tmp_path):
    with pytest.raises(FileNotFoundError):
        factory.generate_blueprint("add a billing agent to nosuchbase", name="x")


def test_generate_blueprint_extend_duplicate_role_rejected(tmp_path):
    # 'boss' already exists in the sales blueprint -> ValueError, no directory
    with pytest.raises(ValueError):
        factory.generate_blueprint("add a boss to sales", name="sales-boss2")
    assert not (tmp_path / "blueprints" / "sales-boss2").exists()


def test_generate_blueprint_scaffold(tmp_path):
    root, meta = factory.generate_blueprint("3-agent lawn-care team: dispatcher, field-agent, boss")
    assert meta["source"] == "scaffold"
    text = (root / "team.yaml").read_text()
    assert "dispatcher" in text and "field-agent" in text and "boss" in text
    assert (root / "souls" / "dispatcher.md").exists()
    assert (root / "assets" / "mesh.json").exists()
    assert (root / "crons.yaml").exists()


def test_blueprint_root_confinement(tmp_path, monkeypatch):
    """Writing must stay inside the owner-configured root."""
    outside = tmp_path / "outside"
    outside.mkdir()
    monkeypatch.setattr(factory, "_owner_blueprint_root", lambda home=None: outside)
    root, meta = factory.generate_blueprint("add a billing agent to sales", name="confined")
    assert root == outside / "confined"
    assert root.is_dir()


def test_owner_root_defaults_under_home_plugin_data(tmp_path):
    """Round 2 #5: factory default root is the TARGET home's plugin-data
    dir — generated blueprints never write into the bundled blueprints dir."""
    assert (_REAL_OWNER_ROOT(tmp_path)
            == tmp_path / "plugin-data" / "teamops" / "blueprints")


def test_generate_refuses_bundled_slug(tmp_path, monkeypatch):
    """Round 2 #5: a slug matching a bundled blueprint is refused outright —
    the rmtree path can never delete a shipped blueprint."""
    with pytest.raises(ValueError, match="bundled blueprint names are reserved"):
        factory.generate_blueprint("add a billing agent to sales", name="sales")
    with pytest.raises(ValueError, match="bundled blueprint names are reserved"):
        factory.generate_blueprint("whatever", name="trading")
    assert not (tmp_path / "blueprints" / "sales").exists()
    assert not (tmp_path / "blueprints" / "trading").exists()


def test_bake_fixture_from_sales(tmp_path):
    dst = factory.bake_fixture_from("sales", target_name="bake-test")
    assert (dst / "team.yaml").exists()
    import shutil
    shutil.rmtree(dst)
