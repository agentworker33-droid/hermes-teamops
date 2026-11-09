"""Standup digest: last-Nh outcomes + open kanban (D2)."""
from teamops import standup

from conftest import FakeHome, now_iso


def _job(jid: str, name: str, **kw) -> dict:
    base = {
        "id": jid, "name": name, "enabled": True, "model": "deepseek/deepseek-v4-flash-0731",
        "provider": "deepseek", "last_status": "ok", "last_error": None,
        "failure_streak": 0, "schedule": {"expr": "0 9 * * *"},
    }
    base.update(kw)
    return base


def test_digest_counts_runs_and_failures(tmp_path):
    home = tmp_path / "home"
    fh = (FakeHome(home)
          .add_job(_job("p1", "morning"), scope="sales-dev")
          .add_execution("sales-dev", "p1", status="completed", started_at=now_iso(-2))
          .add_execution("sales-dev", "p1", status="failed", started_at=now_iso(-3), error="nope")
          .add_execution("sales-dev", "p1", status="completed", started_at=now_iso(-100))
          .add_kanban("sales", {"t1": {"status": "TODO", "title": "open task"},
                                 "t2": {"status": "DONE", "title": "closed"}})
          .write())
    r = standup.digest(home, hours=24)
    assert r["profiles"][0]["profile"] == "sales-dev"
    assert r["profiles"][0]["runs_24h"] == 2
    assert r["profiles"][0]["ok_24h"] == 1
    assert len(r["profiles"][0]["failed"]) == 1
    assert r["profiles"][0]["failed"][0]["error"] == "nope"
    assert len(r["open_kanban"]) == 1
    assert r["open_kanban"][0]["id"] == "t1"
    assert "1 open kanban" in r["summary"]


def test_digest_team_filter(tmp_path):
    home = tmp_path / "home"
    fh = (FakeHome(home)
          .add_job(_job("s1", "sale"), scope="sales-dev")
          .add_job(_job("f1", "fin"), scope="finance-analyst")
          .add_execution("sales-dev", "s1", status="completed", started_at=now_iso(-1))
          .add_execution("finance-analyst", "f1", status="completed", started_at=now_iso(-1))
          .write())
    r = standup.digest(home, team="sales")
    assert [p["profile"] for p in r["profiles"]] == ["sales-dev"]


def test_digest_empty_home(tmp_path):
    home = tmp_path / "home"
    (home / "profiles").mkdir(parents=True)
    r = standup.digest(home)
    assert r["profiles"] == []
    assert "0 open kanban" in r["summary"]
