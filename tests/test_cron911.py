"""Cron911: silent gaps + failures + dead models (D2)."""
from teamops import cron911

from conftest import FakeHome, now_iso


def _job(jid: str, name: str, **kw) -> dict:
    base = {
        "id": jid, "name": name, "enabled": True, "model": "deepseek/deepseek-v4-flash-0731",
        "provider": "deepseek", "last_status": "ok", "last_error": None,
        "failure_streak": 0, "schedule": {"expr": "0 9 * * *"},
    }
    base.update(kw)
    return base


def test_classify_cadence():
    assert cron911.classify_cadence("0 9 * * *") == "daily"
    assert cron911.classify_cadence("0 * * * *") == "hourly"
    assert cron911.classify_cadence("*/15 * * * *") == "subhourly"
    assert cron911.classify_cadence("0 9 * * 1-5") == "weekdays"
    assert cron911.classify_cadence("0 9 * * 0") == "weekly"
    assert cron911.classify_cadence("0 9 1 * *") == "monthly"
    assert cron911.classify_cadence("garbage") == "unknown"


def test_scan_healthy_job_has_no_issues(tmp_path):
    home = tmp_path / "home"
    FakeHome(home).add_job(_job("h1", "ok-job", last_run_at=now_iso(-1))).write()
    r = cron911.scan(home)
    assert r["healthy"] is True
    assert r["jobs_total"] == 1


def test_scan_flags_failure_streak(tmp_path):
    home = tmp_path / "home"
    FakeHome(home).add_job(_job("h1", "bad-job", failure_streak=5,
                                last_status="error", last_error="boom")).write()
    r = cron911.scan(home)
    assert r["healthy"] is False
    assert any("failure_streak=5" in i["detail"] for i in r["issues"])


def test_scan_flags_silent_gap_for_daily_job(tmp_path):
    home = tmp_path / "home"
    # last ran 3 days ago; daily cadence expects <= 2 days
    FakeHome(home).add_job(_job("h1", "gap-job", last_run_at=now_iso(-72))).write()
    r = cron911.scan(home)
    assert any("SILENT GAP" in i["detail"] for i in r["issues"])


def test_scan_flags_never_ran_enabled_job(tmp_path):
    home = tmp_path / "home"
    FakeHome(home).add_job(_job("h1", "never-job")).write()
    r = cron911.scan(home)
    assert any("NEVER ran" in i["detail"] for i in r["issues"])


def test_scan_flags_dead_model_marker(tmp_path):
    home = tmp_path / "home"
    FakeHome(home).add_job(_job("h1", "dead-model-job",
                                last_status="error",
                                last_error="RuntimeError: HTTP 404: This model is no longer free.")).write()
    r = cron911.scan(home)
    assert any("DEAD MODEL" in i["detail"] for i in r["issues"])


def test_scan_skips_silent_gap_for_disabled(tmp_path):
    home = tmp_path / "home"
    FakeHome(home).add_job(_job("h1", "paused-job", enabled=False, last_run_at=now_iso(-200))).write()
    r = cron911.scan(home)
    assert all("SILENT GAP" not in i["detail"] for i in r["issues"])
    assert any("DISABLED" in i["detail"] for i in r["info"])


def test_scan_respects_db_override(tmp_path):
    home = tmp_path / "home"
    # job has no last_run_at; with an executions row in an overridden DB it is NOT "never ran"
    alt_db = tmp_path / "alt" / "executions.db"
    fh = (FakeHome(home).add_job(_job("h1", "db-job"))
          .add_execution("home", "h1", status="completed", started_at=now_iso(-1)))
    fh.write()
    # move the db to the override path (simulate caller-supplied DB)
    import shutil
    alt_db.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(home / "cron" / "executions.db"), str(alt_db))
    r = cron911.scan(home, db_override=alt_db)
    assert all("NEVER ran" not in i["detail"] for i in r["issues"])


def test_merge_issues_collapses_same_job_to_highest_severity():
    issues = [
        {"severity": "MED", "scope": "boss", "job": "pm-brief", "detail": "last_status=error"},
        {"severity": "HIGH", "scope": "boss", "job": "pm-brief", "detail": "failure_streak=5"},
        {"severity": "INFO", "scope": "boss", "job": "pm-brief", "detail": "last_error=boom"},
    ]
    out = cron911.merge_issues(issues)
    assert len(out) == 1
    assert out[0]["severity"] == "HIGH"
    assert out[0]["job"] == "pm-brief"
    assert "failure_streak=5" in out[0]["detail"]
    assert "last_status=error" in out[0]["detail"]


def test_merge_issues_drops_substring_duplicates_and_sorts():
    issues = [
        {"severity": "HIGH", "scope": "home", "job": "gap", "detail": "SILENT GAP: last run 30h ago"},
        {"severity": "HIGH", "scope": "home", "job": "gap", "detail": "SILENT GAP: last run 30h ago (2.0d ago)"},
        {"severity": "HIGH", "scope": "a", "job": "z", "detail": "z first"},
        {"severity": "HIGH", "scope": "a", "job": "b", "detail": "b first"},
    ]
    out = cron911.merge_issues(issues)
    jobs = [(i["scope"], i["job"]) for i in out]
    assert jobs == [("a", "b"), ("a", "z"), ("home", "gap")]
    gap = next(i for i in out if i["job"] == "gap")
    assert gap["detail"].count("SILENT GAP") == 1  # substring dup dropped


def test_scan_output_is_merged_per_job(tmp_path):
    home = tmp_path / "home"
    # one job that trips both failure-streak AND last_error rules -> one merged row
    FakeHome(home).add_job(_job("h1", "multi-bad", failure_streak=4,
                                last_status="error", last_error="boom")).write()
    r = cron911.scan(home)
    rows = [i for i in r["issues"] if i["job"] == "multi-bad"]
    assert len(rows) == 1
    assert rows[0]["severity"] == "HIGH"
    assert "failure_streak=4" in rows[0]["detail"]
    assert "boom" in rows[0]["detail"]
