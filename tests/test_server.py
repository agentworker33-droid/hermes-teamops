"""Live dashboard server: fresh data per request + health + stop."""
import json
import os
import threading
import urllib.error
import urllib.request
from pathlib import Path

from teamops import server

from conftest import FakeHome, now_iso


def _job(jid: str, name: str, **kw) -> dict:
    base = {
        "id": jid, "name": name, "enabled": True,
        "model": "deepseek/deepseek-v4-flash-0731", "provider": "deepseek",
        "last_status": "ok", "last_error": None,
        "failure_streak": 0, "schedule": {"expr": "0 9 * * *"},
    }
    base.update(kw)
    return base


def _serve(home):
    httpd = server.serve(home, port=0)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    return httpd, httpd.server_address[1]


def _token(home):
    return (home / "plugin-data" / "teamops-dashboard" / "server.token").read_text()


def test_server_serves_live_data(tmp_path):
    home = tmp_path / "home"
    fh = FakeHome(home)
    fh.add_job(_job("h1", "home-summary"))
    fh.add_job(_job("b1", "pm-brief", last_status="failed", last_error="boom",
                    failure_streak=2), scope="boss")
    fh.add_job(_job("s1", "daily-push"), scope="sales-dev")
    fh.add_execution("boss", "b1", status="failed", started_at=now_iso(-1), error="boom")
    fh.add_mesh("sales", [{"from": "boss", "to": "sales-dev", "via": "daily-push"}])
    fh.write()
    httpd, port = _serve(home)
    try:
        html = urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=5).read().decode()
        assert "TeamOps Fleet Dashboard" in html
        assert "pm-brief" in html and "failure_streak=2" in html
        headers = urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=5).headers
        assert "no-store" in headers.get("Cache-Control", "")
        health = json.loads(
            urllib.request.urlopen(f"http://127.0.0.1:{port}/health?full=1", timeout=8).read().decode())
        assert health["ok"] is True
        assert health["profiles"] == 2
        assert "sales" in health["teams"]
    finally:
        httpd.shutdown()


def test_server_status_and_stop(tmp_path):
    home = tmp_path / "home"
    home.mkdir(parents=True)
    assert server.running(home) is None
    assert server.status(home)["running"] is False
    assert server.stop(home)["stopped"] is False


def test_server_action_api(tmp_path):
    home = tmp_path / "home"
    fh = FakeHome(home)
    fh.add_job(_job("h1", "home-summary"))
    fh.add_mesh("sales", [{"from": "boss", "to": "sales-dev", "via": "daily-push"}])
    fh.write()
    httpd, port = _serve(home)
    tok = _token(home)
    try:
        def post(payload, token=None):
            headers = {"Content-Type": "application/json"}
            if token is not None:
                headers["X-TeamOps-Token"] = token
            req = urllib.request.Request(
                f"http://127.0.0.1:{port}/api/action",
                data=json.dumps(payload).encode(),
                headers=headers, method="POST")
            return json.loads(urllib.request.urlopen(req, timeout=10).read().decode())

        assert post({"action": "ping"}, token=tok)["ok"] is True
        assert post({"action": "nope"}, token=tok)["ok"] is False
        gen = post({"action": "generate", "payload": {"vertical": "electrician", "prompt": "teamops: generate x"}}, token=tok)
        assert gen["ok"] is True and gen["needs_agent"] is True
        bad = post({"action": "run_script", "payload": {"script": "/tmp/not-a-roster.sh", "args": []}}, token=tok)
        assert bad["ok"] is False
        st = post({"action": "status", "payload": {}}, token=tok)
        assert st["ok"] is True and "sales" in st["text"]
    finally:
        httpd.shutdown()


def test_server_post_requires_per_start_token(tmp_path):
    """Round 2 #3: /api/action is refused without the per-start token."""
    home = tmp_path / "home"
    FakeHome(home).add_job(_job("h1", "home-summary")).write()
    httpd, port = _serve(home)
    try:
        # no token -> 403
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/action",
            data=json.dumps({"action": "ping"}).encode(),
            headers={"Content-Type": "application/json"}, method="POST")
        try:
            urllib.request.urlopen(req, timeout=5)
            assert False, "expected 403 without token"
        except urllib.error.HTTPError as e:
            assert e.code == 403
        # wrong token -> 403
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/action",
            data=json.dumps({"action": "ping"}).encode(),
            headers={"Content-Type": "application/json", "X-TeamOps-Token": "deadbeef"},
            method="POST")
        try:
            urllib.request.urlopen(req, timeout=5)
            assert False, "expected 403 with wrong token"
        except urllib.error.HTTPError as e:
            assert e.code == 403
        # token file exists, 0600, and the served page carries it for the buttons
        tk = home / "plugin-data" / "teamops-dashboard" / "server.token"
        assert tk.exists() and tk.read_text().strip()
        if os.name == "posix":
            assert tk.stat().st_mode & 0o777 == 0o600
        html = urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=5).read().decode()
        assert "window.TEAMOPS_TOKEN=" in html
    finally:
        httpd.shutdown()


def test_server_rejects_non_loopback_host_and_no_cors(tmp_path):
    """Round 2 #3: Host must be 127.0.0.1/localhost; no CORS headers."""
    import http.client
    home = tmp_path / "home"
    FakeHome(home).add_job(_job("h1", "home-summary")).write()
    httpd, port = _serve(home)
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        try:
            conn.request("GET", "/", headers={"Host": "evil.example.com"})
            resp = conn.getresponse()
            assert resp.status == 403
        finally:
            conn.close()
        headers = urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=5).headers
        assert "Access-Control-Allow-Origin" not in headers
        assert "Access-Control-Allow-Methods" not in headers
        hdr = urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=5).headers
        assert "X-TeamOps-Build" in hdr  # still tagged live
    finally:
        httpd.shutdown()


def test_server_last_run_confined_to_team_dir(tmp_path):
    """Round 2 #3: last_run only reads <home>/teams/<team>/ with a strict team charset."""
    home = tmp_path / "home"
    fh = FakeHome(home)
    fh.add_job(_job("h1", "home-summary"))
    (home / "teams" / "sales").mkdir(parents=True)
    (home / "teams" / "sales" / "run.log").write_text("line1\nline2\n")
    # a decoy outside the team dir that must never be read
    (home / "secret.txt").write_text("TOP-SECRET")
    fh.write()
    httpd, port = _serve(home)
    tok = _token(home)
    try:
        def post(payload):
            req = urllib.request.Request(
                f"http://127.0.0.1:{port}/api/action",
                data=json.dumps(payload).encode(),
                headers={"Content-Type": "application/json", "X-TeamOps-Token": tok},
                method="POST")
            return json.loads(urllib.request.urlopen(req, timeout=10).read().decode())

        bad_team = post({"action": "last_run", "payload": {"team": "../../etc", "script": ""}})
        assert bad_team["ok"] is False and "invalid team" in bad_team["error"]
        traversal = post({"action": "last_run", "payload": {"team": "sales", "script": "../.."}})
        assert traversal["ok"] is False and "outside the team dir" in traversal["error"]
        ok = post({"action": "last_run", "payload": {"team": "sales"}})
        assert ok["ok"] is True and "run.log" in ok["text"]
        assert "TOP-SECRET" not in ok["text"]
    finally:
        httpd.shutdown()


def test_server_deploy_action_disabled(tmp_path):
    """Round 2 #3: deploy on the live server is disabled — CLI approval only."""
    home = tmp_path / "home"
    FakeHome(home).add_job(_job("h1", "home-summary")).write()
    httpd, port = _serve(home)
    tok = _token(home)
    try:
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/action",
            data=json.dumps({"action": "deploy", "payload": {"name": "sales"}}).encode(),
            headers={"Content-Type": "application/json", "X-TeamOps-Token": tok},
            method="POST")
        out = json.loads(urllib.request.urlopen(req, timeout=10).read().decode())
        assert out["ok"] is False and "disabled" in out["error"]
    finally:
        httpd.shutdown()


def test_server_run_script_minimal_env(tmp_path, monkeypatch):
    """Round 2 #3: run_script spawns with a minimal PATH/HOME/LANG env, and
    roster entries resolve as bare names under $HOME (real dashboard path)."""
    from teamops import fleet, server as _server
    monkeypatch.setattr(Path, "home", lambda: tmp_path)  # fake $HOME for this test
    bare = "roster.sh"
    script = tmp_path / bare
    script.write_text("#!/bin/bash\necho hi\n")
    monkeypatch.setattr(fleet, "EPHEMERAL_TEAMS",
                        [(bare, "roster", "x", "y", "z")])
    captured = {}

    class FakePopen:
        def __init__(self, cmd, **kw):
            captured["cmd"] = cmd
            captured["env"] = kw.get("env")
            self.pid = 123

    monkeypatch.setattr(_server.subprocess, "Popen", FakePopen)
    out = _server._dispatch_action(tmp_path, "run_script",
                                   {"script": bare, "args": []})
    assert out["ok"] is True, out
    assert captured["cmd"] == ["bash", str(script)]
    assert set(captured["env"]) == {"PATH", "HOME", "LANG"}
    # a non-roster script is still refused
    out = _server._dispatch_action(tmp_path, "run_script",
                                   {"script": "other.sh", "args": []})
    assert out["ok"] is False and "roster" in out["error"]


def test_server_health_cheap_and_404(tmp_path):
    home = tmp_path / "home"
    FakeHome(home).add_job(_job("h1", "home-summary")).write()
    httpd, port = _serve(home)
    try:
        # liveness endpoint without ?full=1 stays cheap (no dashboard build)
        health = json.loads(
            urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=5).read().decode())
        assert health["ok"] is True
        assert "profiles" not in health
        # unknown paths 404
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/nope", timeout=5)
            assert False, "expected 404"
        except urllib.error.HTTPError as e:
            assert e.code == 404
        # pidfile written by serve() -> running() resolves while alive
        assert server.running(home) is not None
    finally:
        httpd.shutdown()


def test_server_renders_live_changes_between_requests(tmp_path):
    home = tmp_path / "home"
    FakeHome(home).add_job(_job("h1", "before")).write()
    httpd, port = _serve(home)
    try:
        first = urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=5).read().decode()
        assert "before" in first
        # mutate the home under the server -> next GET reflects it (never stale)
        import json as _json
        jp = home / "cron" / "jobs.json"
        doc = _json.loads(jp.read_text())
        doc["jobs"].append(_job("h2", "after", last_status="error", last_error="boom",
                                failure_streak=1))
        jp.write_text(_json.dumps(doc))
        second = urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=5).read().decode()
        assert "after" in second
        assert "TeamOps Fleet Dashboard" in second
    finally:
        httpd.shutdown()
