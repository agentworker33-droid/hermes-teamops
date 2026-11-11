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


# ---- theme bridging (Task 2.13) ----

def test_render_html_theme_override_injected(tmp_path):
    home = _home_with_fleet(tmp_path)
    data = dashboard.collect(home)
    theme = {"name": "cyberpunk", "label": "Cyberpunk", "bg": "#000a00",
             "mid": "#00ff41", "fg": "#ffffff", "glow": "rgba(0,255,65,.3)",
             "overrides": {"success": "#00ff88", "warning": "#ffd700", "destructive": "#ff0055"}}
    text = dashboard.render_html(data, theme=theme)
    assert 'id="teamops-theme"' in text
    assert "--bg: #000a00" in text
    assert "--txt: #00ff41" in text
    assert "--accent: #00ff41" in text
    assert "theme Cyberpunk" in text  # rail foot label


def test_render_html_no_theme_keeps_default(tmp_path):
    home = _home_with_fleet(tmp_path)
    data = dashboard.collect(home)
    text = dashboard.render_html(data)
    assert 'id="teamops-theme"' not in text
    assert "--bg: #050505" in text  # the base :root default


def test_load_hermes_theme_builtin(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    (home / "config.yaml").write_text("dashboard:\n  theme: mono\n")
    theme = dashboard.load_hermes_theme(home)
    assert theme is not None
    assert theme["label"] == "Mono"
    assert theme["bg"] == "#0e0e0e"


def test_load_hermes_theme_default_teal(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    (home / "config.yaml").write_text("dashboard:\n  theme: default\n")
    theme = dashboard.load_hermes_theme(home)
    assert theme is not None
    assert theme["label"] == "Hermes Teal"
    assert theme["bg"] == "#041c1c"


def test_load_hermes_theme_missing_or_unknown(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    assert dashboard.load_hermes_theme(home) is None  # no config
    (home / "config.yaml").write_text("dashboard:\n  theme: does-not-exist\n")
    assert dashboard.load_hermes_theme(home) is None  # unknown name


def test_load_hermes_theme_user_yaml(tmp_path):
    try:
        import yaml  # noqa: F401
    except ImportError:
        import pytest
        pytest.skip("yaml not importable")
    home = tmp_path / "home"
    themes_dir = home / "dashboard-themes"
    themes_dir.mkdir(parents=True)
    (themes_dir / "night-ops.yaml").write_text(
        "name: night-ops\n"
        "label: Night Ops\n"
        "palette:\n"
        "  background: {hex: '#101820', alpha: 1}\n"
        "  midground: {hex: '#ffee99', alpha: 1}\n"
        "  foreground: {hex: '#ffffff', alpha: 0}\n"
        "colorOverrides:\n"
        "  success: '#88ffaa'\n"
    )
    (home / "config.yaml").write_text("dashboard:\n  theme: night-ops\n")
    theme = dashboard.load_hermes_theme(home)
    assert theme is not None
    assert theme["name"] == "night-ops"
    assert theme["bg"] == "#101820"
    assert theme["overrides"]["success"] == "#88ffaa"


# ---- needs-you strip (Task 2.14) ----

def test_needs_strip_present_with_failed_run_and_cron_flags(tmp_path):
    home = _home_with_fleet(tmp_path)
    d = home / "plugin-data" / "teamops-dashboard" / "runs"
    d.mkdir(parents=True)
    (d / "r1.json").write_text(json.dumps({
        "id": "r1", "state": "failed", "title": "deploy x", "team": "sales",
        "nodes": [{"id": "n", "label": "n", "state": "failed", "deps": []}],
    }))
    data = dashboard.collect(home)
    kinds = [n["kind"] for n in data["needs"]]
    assert "run" in kinds
    assert any(n["title"].startswith("Run:") for n in data["needs"])
    html = dashboard.render_html(data)
    assert 'id="needs-w"' in html
    assert "need you" in html
    assert "data-jump" in html


def test_needs_strip_absent_when_clean(tmp_path):
    home = tmp_path / "home"
    (home / "cron").mkdir(parents=True)
    (home / "cron" / "jobs.json").write_text('{"jobs": []}')
    data = dashboard.collect(home)
    assert data["needs"] == []
    html = dashboard.render_html(data)
    assert 'id="needs-w"' not in html
