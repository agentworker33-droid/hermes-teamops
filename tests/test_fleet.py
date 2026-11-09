"""Fleet reads: profile_dirs, jobs, executions, memory, mesh (D2)."""
from datetime import datetime, timedelta, timezone

from teamops import fleet

from conftest import FakeHome, now_iso


def _job(jid: str, name: str, **kw) -> dict:
    base = {
        "id": jid, "name": name, "enabled": True, "model": "deepseek/deepseek-v4-flash-0731",
        "provider": "deepseek", "last_status": "ok", "last_error": None,
        "failure_streak": 0, "schedule": {"expr": "0 9 * * *"},
    }
    base.update(kw)
    return base


def test_profile_dirs_skips_hidden(tmp_path):
    home = tmp_path / "home"
    (home / "profiles" / "boss").mkdir(parents=True)
    (home / "profiles" / ".deleted").mkdir(parents=True)
    (home / "profiles" / "default").mkdir(parents=True)
    names = [p.name for p in fleet.profile_dirs(home)]
    assert "boss" in names and "default" in names
    assert ".deleted" not in names


def test_all_jobs_includes_profile_scopes(tmp_path):
    home = tmp_path / "home"
    fh = (FakeHome(home)
          .add_job(_job("h1", "home-job"))
          .add_job(_job("p1", "profile-job"), scope="sales-dev")
          .write())
    scopes = {s for s, _ in fleet.all_jobs(home)}
    assert scopes == {"home", "sales-dev"}


def test_execution_stats_counts_recent_and_failures(tmp_path):
    home = tmp_path / "home"
    fh = (FakeHome(home)
          .add_execution("home", "h1", status="completed", started_at=now_iso(-1))
          .add_execution("home", "h1", status="failed", started_at=now_iso(-2), error="boom")
          .add_execution("home", "h1", status="completed", started_at=now_iso(-50))
          .write())
    stats = fleet.execution_stats(home, "home", hours=24)
    assert stats["total"] == 3
    assert stats["ok"] == 2
    assert stats["failed"] == 1
    assert stats["recent"] == 2


def test_memory_usage_reports_pct_and_skills(tmp_path):
    home = tmp_path / "home"
    pd = home / "profiles" / "boss"
    (pd / "memories").mkdir(parents=True)
    (pd / "memories" / "MEMORY.md").write_text("x" * 1100)  # 50% of 2200 cap
    (pd / "skills" / "foo").mkdir(parents=True)
    (pd / "skills" / "bar").mkdir(parents=True)
    (pd / "skills" / "foo" / "SKILL.md").write_text("# foo")
    (pd / "skills" / "bar" / "SKILL.md").write_text("# bar")
    m = fleet.memory_usage(pd)
    assert m["memory_pct"] == 50.0
    assert m["skills"] == 2


def test_fleet_report_counts_profiles_crons_and_kanban(tmp_path):
    home = tmp_path / "home"
    fh = (FakeHome(home)
          .add_job(_job("h1", "home-job"))
          .add_job(_job("p1", "profile-job"), scope="sales-dev")
          .add_kanban("sales", {"t1": {"status": "TODO", "title": "do it"},
                                 "t2": {"status": "DONE", "title": "done"}})
          .add_mesh("sales", [{"from": "boss", "to": "sales-dev", "via": "daily-push"}])
          .write())
    r = fleet.fleet_report(home)
    assert r["profile_count"] == 1
    assert r["home_crons"] == 1
    assert r["teams"]["sales"]["kanban"]["tasks"] == 2
    assert r["teams"]["sales"]["kanban"]["done"] == 1
    assert "boss -->|daily-push| sales-dev" in r["graph"]


def test_parse_mesh_edges_from_markdown_table():
    text = (
        "| E1 | boss → sales-dev | daily-push |\n"
        "| E2 | sales-dev --> research-analyst | insights |\n"
        "| junk |\n"
        "```mermaid\n"
        "boss --> finance-analyst\n"
        "```\n"
    )
    edges = fleet.parse_mesh_edges(text)
    pairs = {(e["from"], e["to"]) for e in edges}
    assert ("boss", "sales-dev") in pairs
    assert ("sales-dev", "research-analyst") in pairs
    assert ("boss", "finance-analyst") in pairs
    via = {e["via"] for e in edges if e["from"] == "boss" and e["to"] == "sales-dev"}
    assert via == {"daily-push"}


def test_mesh_map_includes_root_level_team_dirs(tmp_path):
    home = tmp_path / "home"
    teams = home / "teams"
    (teams / "sales").mkdir(parents=True)
    (home / "trading-desk").mkdir()
    (teams / "sales" / "mesh.json").write_text('{"edges": [{"from": "boss", "to": "sales-dev"}]}')
    (home / "trading-desk" / "MESH-MAP.md").write_text("| E1 | pm → executor | brief |\n")
    (home / "profiles").mkdir()
    m = fleet.mesh_map(home)
    assert "sales" in m and "trading-desk" in m
    assert m["trading-desk"]["mesh"]["edges"][0]["from"] == "pm"
    # pure profile/config dirs are not teams
    assert "profiles" not in m


def test_enrich_teams_groups_profiles_and_adds_stem_groups():
    teams = {
        "sales": {"dir": "sales", "mesh": {}, "kanban": None, "mesh_map": None},
        "trading-desk": {"dir": "trading-desk", "mesh": {}, "kanban": None, "mesh_map": None},
    }
    profiles = [
        {"name": "boss", "crons": 1, "executions_24h": {"total": 2, "ok": 2, "failed": 0},
         "memory": {"memory_pct": 10.0, "skills": 3}},
        {"name": "sales-dev", "crons": 2, "executions_24h": {"total": 1, "ok": 1, "failed": 0},
         "memory": {"memory_pct": 20.0, "skills": 1}},
        {"name": "sales-support", "crons": 1, "executions_24h": {"total": 3, "ok": 2, "failed": 1},
         "memory": {"memory_pct": 30.0, "skills": 4}},
        {"name": "trading-pm", "crons": 2, "executions_24h": {"total": 2, "ok": 2, "failed": 0},
         "memory": {"memory_pct": 40.0, "skills": 2}},
        {"name": "trading-executor", "crons": 1, "executions_24h": {"total": 1, "ok": 1, "failed": 0},
         "memory": {"memory_pct": 50.0, "skills": 2}},
    ]
    out = fleet.enrich_teams(teams, profiles)
    # sales team absorbs sales-* stem
    assert set(out["sales"]["members"]) == {"sales-dev", "sales-support"}
    assert out["sales"]["stats"]["members"] == 2
    assert out["sales"]["stats"]["crons"] == 3
    assert out["sales"]["stats"]["exec_24h"]["failed"] == 1
    assert out["sales"]["stats"]["memory_pct"] == 25.0
    # trading-desk absorbs trading-* stem
    assert set(out["trading-desk"]["members"]) == {"trading-pm", "trading-executor"}
    # boss alone has no stem group (<2 members) and is not a synthetic team
    assert "boss" not in out


def test_ephemeral_teams_inventories_home_scripts(tmp_path, monkeypatch):
    monkeypatch.setattr(fleet.Path, "home", staticmethod(lambda: tmp_path))
    monkeypatch.setattr(fleet, "_script_running", staticmethod(lambda _s: False))
    # research-swarm with a competitors preset block + a plain script
    (tmp_path / "research-swarm.sh").write_text(
        "LANES_COMPETITORS=(\n"
        "  \"market research\",\n"
        "  \"competitor analysis\",\n"
        ")\n"
    )
    (tmp_path / "content-engine.sh").write_text("#!/bin/bash\necho hi\n")
    (tmp_path / "agentic-coding-team.sh").write_text("#!/bin/bash\necho code\n")
    teams = {t["name"] for t in fleet.ephemeral_teams(tmp_path)}
    assert teams == {"research-swarm", "content-engine", "agentic-coding"}
    swarm = next(t for t in fleet.ephemeral_teams(tmp_path) if t["name"] == "research-swarm")
    assert swarm["status_word"] == "READY"
    assert swarm["sub_agents"] == 2
    assert swarm["hub"] == "synthesizer"
    assert "synthesizer" in swarm["roles"]
    coding = next(t for t in fleet.ephemeral_teams(tmp_path) if t["name"] == "agentic-coding")
    assert "Factory" in coding["lanes"] and "agent-factory" in coding["lanes"]["Factory"][2]


def test_available_blueprints_lists_plugin_and_templates(tmp_path):
    home = tmp_path / "home"
    (home / "templates" / "insurance-claims").mkdir(parents=True)
    (home / "templates" / ".hidden").mkdir()
    r = fleet.available_blueprints(home)
    ids = {b["id"] for b in r["blueprints"]}
    assert {"sales", "property-mgmt", "lawn-care-ops"} <= ids
    assert "fixtures" not in ids  # fixture dir is not a first-class blueprint
    sales = next(b for b in r["blueprints"] if b["id"] == "sales")
    assert sales["roles"] and sales["version"]
    assert r["templates"] == ["insurance-claims"]
