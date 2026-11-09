"""Dashboard (Task 2.8): golden HTML from FakeHome, escaping, tool/CLI path."""
import json
from pathlib import Path

from teamops import dashboard, tools

from conftest import FakeHome, now_iso


def _job(jid: str, name: str, **kw) -> dict:
    base = {
        "id": jid, "name": name, "enabled": True, "model": "deepseek/deepseek-v4-flash-0731",
        "provider": "deepseek", "last_status": "ok", "last_error": None,
        "failure_streak": 0, "schedule": {"expr": "0 9 * * *"},
    }
    base.update(kw)
    return base


def _home_with_fleet(tmp_path) -> Path:
    home = tmp_path / "home"
    fh = FakeHome(home)
    fh.add_job(_job("h1", "home-summary"))
    fh.add_job(_job("b1", "pm-brief", last_status="failed", last_error="boom",
                    failure_streak=2), scope="boss")
    fh.add_job(_job("s1", "daily-push"), scope="sales-dev")
    fh.add_execution("boss", "b1", status="failed", started_at=now_iso(-1), error="boom")
    fh.add_execution("sales-dev", "s1", status="completed", started_at=now_iso(-1))
    boss_dir = home / "profiles" / "boss"
    (boss_dir / "memories").mkdir(parents=True, exist_ok=True)
    (boss_dir / "memories" / "MEMORY.md").write_text("x" * 1100)  # 50% of 2200
    (boss_dir / "memories" / "USER.md").write_text("y" * 1100)
    (boss_dir / "SOUL.md").write_text("# Boss soul")
    (boss_dir / "skills" / "ops").mkdir(parents=True)
    (boss_dir / "skills" / "ops" / "SKILL.md").write_text("# ops")
    (home / "profiles" / "sales-dev" / "SOUL.md").write_text("# Sales soul")
    fh.add_kanban("sales", {"t1": {"status": "TODO", "title": "do it"},
                            "t2": {"status": "DONE", "title": "done"}})
    fh.add_mesh("sales", [{"from": "boss", "to": "sales-dev", "via": "daily-push"}])
    fh.write()
    return home


def test_write_dashboard_golden_markers(tmp_path):
    home = _home_with_fleet(tmp_path)
    result = dashboard.write_dashboard(home)
    html_path = Path(result["html_path"])
    assert html_path.exists()
    assert result["bytes"] > 5000
    assert result["profiles"] == 2
    assert result["teams"] == ["sales"]
    text = html_path.read_text(encoding="utf-8")
    # title/header + home path + timestamp
    assert "TeamOps Fleet Dashboard" in text
    assert str(home) in text
    assert "generated" in text.lower()
    # profile panel: names, SOUL chip, memory bar
    assert "boss" in text
    assert "sales-dev" in text
    assert "SOUL" in text
    assert "bar low" in text and "50.0%" in text
    assert "1 skill(s)" in text
    # execution stats
    assert "Executions 24h" in text
    # cron health flags from Cron911 (failure streak + NEVER ran info)
    assert "Cron health" in text
    assert "pm-brief" in text
    assert "failure_streak=2" in text
    # kanban + interactive mesh svg
    assert "Teams &amp; mesh" in text
    assert "daily-push" in text
    assert "<svg" in text and 'class="mesh-svg"' in text
    assert 'data-name="boss"' in text
    assert 'data-name="sales-dev"' in text
    # clickable JS is inline and no external resources
    assert "<script>" in text and "addEventListener('click'" in text
    assert "<script src=" not in text
    assert "https://" not in text
    assert "cdn" not in text.lower()
    # document closes properly
    assert text.strip().endswith("</html>")


def test_dashboard_escapes_user_content(tmp_path):
    home = tmp_path / "home"
    evil_scope = "boss"
    fh = FakeHome(home)
    fh.add_job(_job("e1", 'job<script>alert(1)</script>',
                    last_status="failed", last_error="boom<script>alert(2)</script>",
                    failure_streak=1), scope=evil_scope)
    fh.add_mesh("sales", [{"from": "boss", "to": "sales-dev",
                           "via": '<img src=x onerror=alert(3)>'}])
    fh.write()
    text = dashboard.render_html(dashboard.collect(home))
    # raw HTML must never survive user content
    assert "<script>alert(1)</script>" not in text
    assert "<script>alert(2)</script>" not in text
    assert "<img src=x onerror=alert(3)>" not in text
    # escaped forms are present
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in text
    assert "&lt;script&gt;alert(2)&lt;/script&gt;" in text
    assert "&lt;img src=x onerror=alert(3)&gt;" in text


def test_dashboard_degrades_without_jobs(tmp_path):
    home = tmp_path / "bare"
    home.mkdir(parents=True)
    result = dashboard.write_dashboard(home)
    text = Path(result["html_path"]).read_text(encoding="utf-8")
    assert "teamops-dashboard.html" in result["html_path"]
    assert "no profiles deployed" in text
    # cron scan succeeds with zero jobs -> clean, not broken
    assert "No cron issues detected." in text
    assert "no teams / mesh yet" in text
    assert result["cron_issues"] == 0
    # explicit degrade: if the cron scan is unavailable the section says so
    bare = dashboard.render_html({"home": str(home), "profiles": [], "teams": {},
                                  "home_executions_24h": {"total": 0, "ok": 0, "failed": 0},
                                  "cron": None})
    assert "cron scan unavailable" in bare


def test_dashboard_team_filter_filename(tmp_path):
    home = tmp_path / "home"
    FakeHome(home).add_mesh("sales", [{"from": "a", "to": "b", "via": "x"}]).write()
    result = dashboard.write_dashboard(home, team_filter="sales")
    assert result["html_path"].endswith("teamops-dashboard-sales.html")


def test_tool_team_status_html_returns_path(tmp_path):
    home = _home_with_fleet(tmp_path)
    out = json.loads(tools.team_status(home=str(home), html=True))
    assert out["success"] is True
    assert Path(out["html_path"]).exists()
    assert "title" in out
    # default (non-html) still returns the JSON report
    plain = json.loads(tools.team_status(home=str(home)))
    assert plain["success"] is True
    assert "profiles" in plain and "graph" in plain


def test_cli_status_html_prints_path(capsys, tmp_path):
    from teamops import cli
    home = _home_with_fleet(tmp_path)
    rc = cli.main(["teams", "status", "--html", "--no-open", "--home", str(home)])
    out = capsys.readouterr().out
    assert rc == 0
    payload = json.loads(out)
    assert payload["success"] is True
    assert Path(payload["html_path"]).exists()
    # custom out_path is honored
    custom = tmp_path / "custom" / "dash.html"
    rc2 = cli.main(["teams", "status", "--html", str(custom), "--no-open", "--home", str(home)])
    assert rc2 == 0
    assert custom.exists()


def test_cli_golden_html_default_mode_no_browser_req(capsys, tmp_path):
    """--html without --no-open in a headless env must not crash (webbrowser
    returns False gracefully)."""
    from teamops import cli
    home = _home_with_fleet(tmp_path)
    rc = cli.main(["teams", "status", "--html", "--home", str(home)])
    assert rc == 0
    assert json.loads(capsys.readouterr().out)["success"] is True
