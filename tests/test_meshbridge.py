"""Mesh bridge: MESH-MAP.md append + shared-surface outbox fallback (D2)."""
import json

from teamops import meshbridge


def _make_team_dirs(home, names=("sales",)):
    for n in names:
        (home / "teams" / n).mkdir(parents=True, exist_ok=True)
    return home


def test_append_mesh_map_creates_and_appends(tmp_path):
    home = _make_team_dirs(tmp_path / "home")
    e1 = meshbridge.append_mesh_map(home, "decision: switch to deepseek flash", author="boss", team="sales")
    assert len(e1) == 1
    mm = home / "teams" / "sales" / "MESH-MAP.md"
    assert mm.exists()
    text = mm.read_text()
    assert "boss" in text and "decision: switch to deepseek flash" in text

    e2 = meshbridge.append_mesh_map(home, "updated: keep paper trading", author="pm")
    assert len(e2) == 1
    text2 = mm.read_text()
    assert "updated: keep paper trading" in text2


def test_append_mesh_map_team_filter(tmp_path):
    home = _make_team_dirs(tmp_path / "home", names=("sales", "property-mgmt"))
    entries = meshbridge.append_mesh_map(home, "hello", author="ops", team="property-mgmt")
    assert [e["team"] for e in entries] == ["property-mgmt"]
    assert not (home / "teams" / "sales" / "MESH-MAP.md").exists()


def test_mnemosyne_shared_falls_back_to_outbox(tmp_path):
    home = _make_team_dirs(tmp_path / "home")
    out = meshbridge.mnemosyne_shared(home, "broadcast test", author="boss")
    assert out["shared"] is False  # mnemosyne plugin not importable in test env
    outbox = home / "teams" / ".mesh-outbox.jsonl"
    assert outbox.exists()
    lines = outbox.read_text().strip().splitlines()
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry["author"] == "boss"
    assert entry["message"] == "broadcast test"


def test_sync_returns_structured_ok(tmp_path):
    home = _make_team_dirs(tmp_path / "home")
    r = meshbridge.sync(home, "use paper trading only", author="dalton", team="sales")
    assert r["success"] is True
    assert r["author"] == "dalton"
    assert len(r["mesh_maps_updated"]) == 1
    assert r["shared"]["shared"] is False
