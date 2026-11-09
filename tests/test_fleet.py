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
