"""Live dashboard server: fresh data per request + health + stop."""
import json
import threading
import urllib.error
import urllib.request

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
    try:
        def post(payload):
            req = urllib.request.Request(
                f"http://127.0.0.1:{port}/api/action",
                data=json.dumps(payload).encode(),
                headers={"Content-Type": "application/json"}, method="POST")
            return json.loads(urllib.request.urlopen(req, timeout=10).read().decode())

        assert post({"action": "ping"})["ok"] is True
        assert post({"action": "nope"})["ok"] is False
        gen = post({"action": "generate", "payload": {"vertical": "electrician", "prompt": "teamops: generate x"}})
        assert gen["ok"] is True and gen["needs_agent"] is True
        bad = post({"action": "run_script", "payload": {"script": "/tmp/not-a-roster.sh", "args": []}})
        assert bad["ok"] is False
        st = post({"action": "status", "payload": {}})
        assert st["ok"] is True and "sales" in st["text"]
    finally:
        httpd.shutdown()


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
