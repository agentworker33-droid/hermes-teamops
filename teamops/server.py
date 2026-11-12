"""Live TeamOps dashboard server — mirrors the mnemosyne-dashboard pattern.

Every ``GET /`` regenerates the dashboard from the CURRENT HERMES_HOME state
(profiles, cron executions, mesh files, ephemeral scripts) — never a stale
snapshot. Also serves ``/health`` for status checks. ``--daemon`` runs it in
the background with a pidfile + log, like the Mnemosyne dashboard plugin.

Security posture (catalog review round 2 #3): loopback-bound (127.0.0.1),
Host header must be 127.0.0.1/localhost/[::1], NO CORS headers, and every
``POST /api/action`` requires the per-start token written 0600 to
``<home>/plugin-data/teamops-dashboard/server.token`` (injected into the
served page for the dashboard's own buttons). Mutating actions that need an
interactive human (deploy) are disabled on the server — CLI approval only.

Usage:
    python -m teamops.server --home ~/.hermes --port 8781 --open   # foreground
    python -m teamops.server --home ~/.hermes --port 8781 --daemon # background
"""
from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import signal
import socket
import subprocess
import sys
import time
import urllib.request
import webbrowser
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import dashboard, fleet, tools

DEFAULT_PORT = 8781


def tokenfile(home: Path) -> Path:
    """Per-start auth token for POST actions (0600, never exposed cross-origin)."""
    return _data_dir(home) / "server.token"


def _inject_token(html: str, token: str) -> str:
    """Embed the per-start token for the dashboard's own action buttons.

    Same-origin pages can read it via JS; a THIRD-PARTY page cannot (no CORS
    headers, and the Host header must be 127.0.0.1/localhost).
    """
    if not token or "</body>" not in html:
        return html
    return html.replace(
        "</body>",
        f'<script>window.TEAMOPS_TOKEN={json.dumps(token)};</script></body>')


def _build(home: Path) -> str:
    data = dashboard.collect(home)
    build_id = (f"{len(data.get('profiles', []))}p-{len(data.get('teams', {}))}t-"
                f"{datetime.now(timezone.utc).strftime('%H%M%S')}")
    return dashboard.render_html(data, build_id=build_id,
                                 theme=dashboard.load_hermes_theme(home))


def _health_data(home: Path, full: bool = False) -> dict:
    base = {
        "ok": True,
        "home": str(home),
        "pid": os.getpid(),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    if not full:
        return base  # cheap: liveness only, no dashboard build
    data = dashboard.collect(home)
    base.update({
        "profiles": data.get("profile_count", 0),
        "teams": list(data.get("teams", {}).keys()),
        "ephemeral": len(data.get("ephemeral", []) or []),
        "cron_flags": len((data.get("cron") or {}).get("issues", []) or []),
    })
    return base


class Handler(BaseHTTPRequestHandler):
    home: Path = None  # set per server instance
    token: str = ""    # per-start auth token for /api/action (review round 2 #3)
    server_version = "TeamOps/0.1"

    def _host_ok(self) -> bool:
        """Only loopback origins (`127.0.0.1:<port>` / `localhost:<port>` / `[::1]`).

        Blocks DNS-rebinding / third-party pages from talking to the local
        dashboard server (review round 2 #3).
        """
        host = (self.headers.get("Host") or "").strip().lower()
        hostname = host.rsplit(":", 1)[0].strip("[]")
        return hostname in ("127.0.0.1", "localhost", "::1")

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        # NOTE: deliberately NO Access-Control-* headers — this is a local
        # loopback server; cross-origin pages must not be able to call actions.
        self.send_header("X-TeamOps-Build", "live")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(204)
        self._send(204, b"", "text/plain")

    def do_POST(self) -> None:  # noqa: N802 — /api/action executes dashboard buttons
        if not self._host_ok():
            self._send(403, b"forbidden host", "text/plain")
            return
        if self.path.split("?", 1)[0] != "/api/action":
            self._send(404, b"not found", "application/json")
            return
        got = self.headers.get("X-TeamOps-Token") or ""
        if not Handler.token or got != Handler.token:
            self._send(403, json.dumps({"ok": False, "error": "missing or invalid server token"}).encode(),
                       "application/json")
            return
        length = int(self.headers.get("Content-Length") or 0)
        if length > 65536:
            self._send(413, json.dumps({"ok": False, "error": "payload too large"}).encode(),
                       "application/json")
            return
        raw = self.rfile.read(length).decode("utf-8") if length else "{}"
        try:
            data = json.loads(raw)
        except ValueError:
            self._send(400, json.dumps({"ok": False, "error": "bad json"}).encode(),
                       "application/json")
            return
        result = _dispatch_action(Handler.home, str(data.get("action", "")), data.get("payload"))
        self._send(200, json.dumps(result).encode("utf-8"), "application/json")

    def do_GET(self) -> None:  # noqa: N802 (http.server API)
        if not self._host_ok():
            self._send(403, b"forbidden host", "text/plain")
            return
        path = self.path.split("?")[0]
        try:
            if path in ("/", "/index.html"):
                html = _inject_token(_build(Handler.home), Handler.token)
                self._send(200, html.encode("utf-8"), "text/html; charset=utf-8")
            elif path == "/health":
                parts = self.path.split("?", 1)[-1].split("&") if "?" in self.path else []
                query_full = any(p.split("=", 1)[0] == "full" for p in parts)
                self._send(200, json.dumps(_health_data(Handler.home, full=query_full)).encode("utf-8"),
                           "application/json")
            elif path == "/favicon.ico":
                self._send(204, b"", "image/x-icon")
            else:
                self._send(404, b"not found", "text/plain")
        except Exception as exc:  # noqa: BLE001 — surface honest errors
            self._send(500, f"teamops server error: {exc}".encode("utf-8"), "text/plain")

    def log_message(self, fmt: str, *args) -> None:  # quiet by default
        pass

    def handle_error(self, request, client_address) -> None:  # quiet broken pipes
        pass


def find_port(start: int = DEFAULT_PORT, tries: int = 20) -> int:
    for p in range(start, start + tries):
        s = socket.socket()
        try:
            s.bind(("127.0.0.1", p))
            return p
        except OSError:
            continue
        finally:
            s.close()
    return start


def _data_dir(home: Path) -> Path:
    d = home / "plugin-data" / "teamops-dashboard"
    d.mkdir(parents=True, exist_ok=True)
    return d


def pidfile(home: Path) -> Path:
    return _data_dir(home) / "server.pid"


def portfile(home: Path) -> Path:
    return _data_dir(home) / "server.port"


def logfile(home: Path) -> Path:
    return _data_dir(home) / "server.log"


def running(home: Path) -> int | None:
    """Return the running server pid, or None."""
    pf = pidfile(home)
    if not pf.exists():
        return None
    try:
        pid = int(pf.read_text().strip())
        os.kill(pid, 0)
        return pid
    except (ValueError, OSError):
        return None


def stop(home: Path) -> dict:
    pid = running(home)
    if not pid:
        return {"ok": True, "stopped": False, "message": "no teamops dashboard server running"}
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        pass
    # give the handler a moment, then force-kill if it is still up
    for _ in range(10):
        if running(home) != pid:
            break
        time.sleep(0.25)
    if running(home) == pid:
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass
    return {"ok": True, "stopped": True, "pid": pid}


def status(home: Path) -> dict:
    pid = running(home)
    port = DEFAULT_PORT
    pf = portfile(home)
    if pf.exists():
        try:
            port = int(pf.read_text().strip())
        except ValueError:
            pass
    return {"ok": True, "running": pid is not None, "pid": pid,
            "url": f"http://127.0.0.1:{port}/" if pid else None}


def start_daemon(home: Path, port: int, open_browser: bool) -> dict:
    """Spawn a detached server; return the URL (waits until it answers)."""
    if running(home):
        return {"ok": True, "started": False, "pid": running(home),
                "url": f"http://127.0.0.1:{port}/"}
    cmd = [sys.executable, "-m", "teamops.server",
           "--home", str(home), "--port", str(port)]
    if open_browser:
        cmd.append("--open")
    log = logfile(home)
    env = dict(os.environ)
    with open(log, "a", encoding="utf-8") as f:
        proc = subprocess.Popen(cmd, stdout=f, stderr=f, env=env,
                                start_new_session=True,
                                cwd=str(Path(__file__).resolve().parent.parent))
    # wait for the child to write its pidfile and answer /health
    deadline = time.time() + 15
    last_error = "timed out"
    while time.time() < deadline:
        if running(home):
            try:
                import urllib.request
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=0.6):
                    pass
                return {"ok": True, "started": True, "pid": running(home),
                        "url": f"http://127.0.0.1:{port}/"}
            except Exception:  # noqa: BLE001
                time.sleep(0.3)
                continue
        time.sleep(0.3)
    return {"ok": False, "error": f"server did not start (log: {log})"}


def serve(home: Path, port: int = DEFAULT_PORT, open_browser: bool = False) -> ThreadingHTTPServer:
    Handler.home = home
    Handler.token = secrets.token_hex(32)
    tk = tokenfile(home)
    tk.parent.mkdir(parents=True, exist_ok=True)
    tk.write_text(Handler.token)
    try:
        os.chmod(tk, 0o600)  # owner-only: contains a live action token
    except OSError:  # pragma: no cover — chmod is best-effort on exotic fs
        pass
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    pf = pidfile(home)
    pf.write_text(str(os.getpid()))
    portfile(home).write_text(str(port))
    logfile(home).parent.mkdir(parents=True, exist_ok=True)

    def _cleanup(*_a):
        # NOTE: httpd.shutdown() deadlocks when called from the same thread as
        # serve_forever (signal handler == main thread), so close + hard-exit.
        try:
            httpd.server_close()
        except Exception:  # noqa: BLE001
            pass
        try:
            pf.unlink(missing_ok=True)
        except Exception:  # noqa: BLE001
            pass
        try:
            portfile(home).unlink(missing_ok=True)
        except Exception:  # noqa: BLE001
            pass
        try:
            tokenfile(home).unlink(missing_ok=True)
        except Exception:  # noqa: BLE001
            pass
        os._exit(0)

    signal.signal(signal.SIGTERM, _cleanup)
    signal.signal(signal.SIGINT, _cleanup)
    if open_browser:
        webbrowser.open(f"http://127.0.0.1:{port}/")
    return httpd


def _dispatch_action(home: Path, action: str, payload: dict | None) -> dict:
    """Execute a dashboard button's action against the LIVE home.

    Returns JSON-ish results; actions that genuinely need an interactive agent
    return ``needs_agent`` with the detailed prompt instead.
    """
    payload = payload or {}
    home = Path(home)

    if action == "ping":
        return {"ok": True, "pong": True}

    if action == "run_script":
        script = str(payload.get("script", ""))
        args = payload.get("args") or []
        allowed = {str(Path.home() / s) for s, _, _, _, _ in fleet.EPHEMERAL_TEAMS}
        p = Path(script).expanduser()
        if not p.is_absolute():
            p = Path.home() / p  # roster entries are bare names under $HOME
        if str(p) not in allowed or not p.exists():
            return {"ok": False, "error": "unknown script — not in the team roster"}
        if not isinstance(args, list) or not all(
                isinstance(a, str) and re.fullmatch(r"[a-zA-Z0-9._/\-]+", a) for a in args):
            return {"ok": False, "error": "bad args"}
        out_dir = home / "plugin-data" / "teamops-dashboard" / "actions"
        out_dir.mkdir(parents=True, exist_ok=True)
        logf = out_dir / f"{p.stem}-{datetime.now().strftime('%Y%m%d-%H%M%S')}.log"
        # minimal child env: PATH/HOME/LANG only — no profile secrets, no shell
        # injection surface via inherited env (review round 2 #3)
        env = {"PATH": os.environ.get("PATH", ""),
               "HOME": str(Path.home()),
               "LANG": os.environ.get("LANG", "C")}
        with open(logf, "w", encoding="utf-8") as f:
            proc = subprocess.Popen(["bash", str(p), *args], stdout=f, stderr=f,
                                    start_new_session=True, env=env)
        return {"ok": True, "started": True, "pid": proc.pid, "log": str(logf),
                "text": f"started (pid {proc.pid}) — output: {logf.name}"}

    if action == "last_run":
        team = str(payload.get("team", ""))
        # CONFINED to <home>/teams/<team>/ + strict team charset (review round 2 #3)
        if not re.fullmatch(r"[a-z0-9-]+", team):
            return {"ok": False, "error": "invalid team — must match ^[a-z0-9-]+$"}
        team_root = (home / "teams" / team).resolve()
        home_root = home.resolve()
        if home_root not in team_root.parents:
            return {"ok": False, "error": "team resolves outside <home>/teams/"}
        sub_agent = str(payload.get("script", ""))
        if sub_agent:
            sub_agent = Path(sub_agent).stem
        candidates = [team_root]
        if sub_agent:
            sub_dir = (team_root / sub_agent).resolve()
            if team_root not in sub_dir.parents and sub_dir != team_root:
                return {"ok": False, "error": "script path resolves outside the team dir"}
            candidates.append(sub_dir)
        newest: tuple[float, Path] | None = None
        for d in candidates:
            if not d.is_dir():
                continue
            for fp in list(d.rglob("*"))[:2000]:
                if fp.is_file() and fp.suffix.lower() in (".log", ".md", ".json", ".txt"):
                    mtime = fp.stat().st_mtime
                    if newest is None or mtime > newest[0]:
                        newest = (mtime, fp)
        if newest is None:
            return {"ok": True, "text": f"no run outputs found for {team or sub_agent} yet"}
        path = newest[1]
        tail = "\n".join(path.read_text(errors="ignore").splitlines()[-40:])
        size = path.stat().st_size
        return {"ok": True, "text": f"{path} ({size:,} bytes) — last 40 lines:\n{tail}"}

    if action == "standup":
        try:
            text = tools.team_standup(team=payload.get("team"), home=str(home))
            return {"ok": True, "text": text}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}

    if action == "status":
        try:
            text = tools.team_status(team=payload.get("team"), home=str(home))
            return {"ok": True, "text": text}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}

    if action == "cron_scan":
        try:
            return {"ok": True, "text": tools.cron_scan()}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}

    if action == "model_scan":
        try:
            return {"ok": True, "text": tools.model_scan(home=str(home))}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}

    if action == "deploy":
        # REMOVED from the live server (review round 2 #3): deploy creates
        # profiles + crons, which needs a HUMAN approval via the CLI. The
        # dashboard button is gone; this branch only answers legacy callers.
        return {"ok": False, "error": "deploy is disabled on the live dashboard — "
                "run 'hermes teams deploy <name>' from the CLI so a human approves "
                "(OWNER ACTION)."}

    if action in ("generate", "create"):
        prompt = str(payload.get("prompt", ""))
        if not prompt:
            return {"ok": False, "error": "missing prompt"}
        return {"ok": True, "needs_agent": True, "prompt": prompt}

    return {"ok": False, "error": f"unknown action: {action}"}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="teamops-server")
    ap.add_argument("--home", default=None, help="HERMES_HOME (default: env or ~/.hermes)")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--daemon", action="store_true", help="run in the background")
    ap.add_argument("--open", action="store_true", help="open the dashboard in a browser")
    ap.add_argument("--stop", action="store_true", help="stop a running daemon")
    a = ap.parse_args(argv)
    home = Path(a.home).expanduser() if a.home else Path(
        os.environ.get("HERMES_HOME", str(Path.home() / ".hermes"))).expanduser()

    if a.stop:
        print(json.dumps(stop(home)))
        return 0
    if a.daemon:
        print(json.dumps(start_daemon(home, a.port, a.open)))
        return 0

    # bind the EXACT requested port — never silently drift to a different one
    # (a drifting port breaks the daemon health-check handshake)
    try:
        serve(home, port=a.port, open_browser=a.open).serve_forever()
    except OSError as exc:
        print(f"teamops-server: could not bind 127.0.0.1:{a.port} ({exc}) — "
              "port in use? stop the other server first (hermes teams dashboard-stop)",
              file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
