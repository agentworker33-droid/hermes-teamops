"""Optional VPS provisioning — SSH transport for team deploy/verify/rollback.

Local-first by design: without ``--host`` nothing in this module runs and the
plugin behaves exactly as before. With ``--host user@host[:port]`` the whole
Deploy Guard flow is executed REMOTELY over ssh:

  1. preflight remote (python3, disk, RAM, home writable, tar)
  2. upload the plugin bundle (tar stream over ssh stdin)
  3. run the same `team_deploy`/`team_verify`/`team_rollback` handlers
     on the remote via a tiny driver script (teamops/scripts/remote-driver.py)
  4. cleanup the staging dir

Credentials & the CREDS-SOURCE rule (boss-live 2026-11-09):
  - The plugin NEVER stores or ships credentials.
  - Auth is the RUNNING USER's own ssh setup: agent + ~/.ssh keys, or an
    explicit `--ssh-key <path>` (-i). Nothing is embedded, nothing is fetched.
  - No creds/keys configured -> ssh fails to authenticate -> the feature
    degrades to a clean FAIL check with a hint. Local deploys keep working at
    100%. Zero nags, zero broken UI (acceptance rule).

All ssh I/O goes through module-level ``run_ssh`` — tests inject a fake runner
so the suite never touches the network.
"""
from __future__ import annotations

import io
import json
import os
import re
import secrets
import shlex
import shutil
import subprocess
import tarfile
from dataclasses import dataclass
from pathlib import Path

from .guard import CheckResult, MIN_FREE_BYTES

MIN_REMOTE_MEM_MB = 512
MIN_PYTHON: tuple[int, int] = (3, 9)

# Catalog review round 2 #1: blueprint names passed into the remote shell are
# validated against a strict charset — never interpolated unquoted.
NAME_RE = re.compile(r"^[a-z0-9-]{1,64}$")

# ssh options: BatchMode => never prompt for a password (clean failure instead),
# ConnectTimeout => fail fast, accept-new => a fresh VPS has no known_hosts entry
# so first-time provisioning must not die on the host key prompt.
SSH_BASE_OPTS = [
    "-o", "BatchMode=yes",
    "-o", "ConnectTimeout=15",
    "-o", "StrictHostKeyChecking=accept-new",
]

# Inline remote preflight (runs over sh, no upload needed).
REMOTE_PREFLIGHT_SH = (
    "python3 --version 2>&1; echo '---'; "
    "df -Pk / 2>/dev/null | awk 'NR==2{print $4}'; echo '---'; "
    "free -m 2>/dev/null | awk '/Mem:/{print $7}'; echo '---'; "
    "if mkdir -p \"$HOME/.hermes\" 2>/dev/null && test -w \"$HOME/.hermes\"; then echo WRITABLE; else echo NOT_WRITABLE; fi; echo '---'; "
    "command -v tar || echo NO_TAR"
)

DRIVER_SCRIPT = Path(__file__).resolve().parent / "scripts" / "remote-driver.py"


def default_run_ssh(command: str, host: str, key: str | None = None,
                    stdin_bytes: bytes | None = None, timeout: int = 120) -> tuple[int, str]:
    """Run `ssh <host> <command>`; returns (exit_code, stdout+stderr).

    ``--`` terminates option parsing before the host so a host/name beginning
    with ``-`` can never be interpreted as an ssh option (review round 2 #1).
    """
    cmd = ["ssh", *SSH_BASE_OPTS]
    if key:
        cmd += ["-i", str(Path(key).expanduser())]
    cmd += ["--", host, command]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, timeout=timeout,
            input=stdin_bytes,  # bytes -> passed straight through
        )
        out = (proc.stdout or b"").decode(errors="replace") + (proc.stderr or b"").decode(errors="replace")
        return proc.returncode, out
    except subprocess.TimeoutExpired:
        return 124, "TIMEOUT"
    except FileNotFoundError:
        return 127, "ssh: command not found"
    except OSError as e:  # noqa: BLE001
        return 2, f"ssh error: {e}"


def parse_host(host: str) -> tuple[str, str, str | None]:
    """Parse 'user@host[:port]' / 'host[:port]'. Defaults user to current user.

    Refuses host specs that begin with ``-`` (ssh option injection) — review
    round 2 #1.
    """
    spec = host.strip()
    if spec.startswith("-"):
        raise ValueError(f"invalid host {host!r}: host must not start with '-'")
    user = os.environ.get("USER") or os.environ.get("USERNAME") or "root"
    port: str | None = None
    if "@" in spec:
        user, _, spec = spec.rpartition("@")
        if not user or not spec or "@" in user:
            raise ValueError(f"invalid host {host!r}: expected [user@]host[:port]")
    if ":" in spec:
        head, _, tail = spec.rpartition(":")
        if tail.isdigit():
            hostname, port = head, tail
        else:  # bare 'host:port' where tail not numeric — treat whole as hostname
            hostname = spec
    else:
        hostname = spec
    if not hostname or any(c in hostname for c in " /@:"):
        raise ValueError(f"invalid host {host!r}: expected [user@]host[:port]")
    if hostname.startswith("-") or user.startswith("-"):
        raise ValueError(f"invalid host {host!r}: host/user must not start with '-'")
    return user, hostname, port


def host_target(host: str) -> str:
    user, hostname, port = parse_host(host)
    return f"{user}@{hostname}" if port is None else f"{user}@{hostname}:{port}"


def transport_available(host: str) -> tuple[bool, str]:
    """Feature-disabled gate: no ssh binary or unparseable host -> clean reason."""
    if shutil.which("ssh") is None:
        return False, "VPS provisioning disabled: ssh not found on PATH — deploy locally with --home instead"
    try:
        parse_host(host)
    except ValueError as e:
        return False, f"VPS provisioning disabled: {e}"
    return True, "ssh available"


@dataclass
class VpsSummary:
    team: str
    checks: list[CheckResult]
    remote: dict = None  # type: ignore[assignment]
    staging: str | None = None
    extra: dict = None  # type: ignore[assignment]

    def to_dict(self, host: str, mode: str = "deploy") -> dict:
        d = {
            "mode": "vps",
            "host": host,
            "team": self.team,
            "checks": [c.__dict__ for c in self.checks],
            "pass": sum(1 for c in self.checks if c.status == "PASS"),
            "fail": sum(1 for c in self.checks if c.status == "FAIL"),
            "skip": sum(1 for c in self.checks if c.status in ("SKIP", "WARN")),
            "green": all(c.status != "FAIL" for c in self.checks),
        }
        if self.remote is not None:
            d["remote"] = self.remote
        if self.staging:
            d["staging"] = self.staging
        if self.extra:
            d.update(self.extra)
        return d


class VpsDeploy:
    """Orchestrates a remote team deploy/verify/rollback over ssh.

    ``run_ssh`` is a module-level seam: tests monkeypatch it with a fake,
    so no real network is ever touched in the suite.
    """

    run_ssh = staticmethod(default_run_ssh)

    def __init__(self, name: str, host: str, key: str | None = None,
                 remote_home: str | None = None):
        if not NAME_RE.match(name or ""):
            raise ValueError(
                f"invalid blueprint name {name!r}: must match ^[a-z0-9-]{{1,64}}$ "
                "(no spaces, slashes or shell metacharacters)")
        self.name = name
        self.host = host
        self.key = key
        self.remote_home = remote_home or "~/.hermes"
        self.staging: str | None = None
        self.checks: list[CheckResult] = []

    # -- helpers ----------------------------------------------------------
    def _ssh(self, command: str, stdin_bytes: bytes | None = None, timeout: int = 120) -> tuple[int, str]:
        return self.run_ssh(command, self.host, key=self.key, stdin_bytes=stdin_bytes, timeout=timeout)

    def _add(self, name: str, status: str, detail: str = "") -> None:
        self.checks.append(CheckResult(name, status, detail))

    def _parse_remote_preflight(self, out: str) -> dict:
        """Split the marker-joined preflight output into {python, disk_kb, mem_mb, home, tar}."""
        parts = [p.strip() for p in out.split("---")]
        data = {}
        if len(parts) >= 5:
            data["python"] = parts[0]
            data["disk_kb"] = parts[1].split()[0] if parts[1].split() else "0"
            data["mem_mb"] = parts[2].split()[0] if parts[2].split() else "0"
            data["home"] = parts[3]
            data["tar"] = parts[4]
        return data

    def _bundle(self) -> bytes:
        """tar.gz of the plugin package (includes blueprints + scripts)."""
        pkg = Path(__file__).resolve().parent  # the teamops/ package dir
        buf = io.BytesIO()
        skip = {"__pycache__", ".pytest_cache"}
        with tarfile.open(fileobj=buf, mode="w:gz") as tf:
            for p in sorted(pkg.rglob("*")):
                if p.is_file() and not any(part in skip for part in p.parts):
                    tf.add(p, arcname=f"teamops/{p.relative_to(pkg)}")
        return buf.getvalue()

    def _driver(self) -> bytes:
        if not DRIVER_SCRIPT.exists():
            raise RuntimeError(f"remote driver script missing: {DRIVER_SCRIPT}")
        return DRIVER_SCRIPT.read_bytes()

    # -- 1. preflight -------------------------------------------------------
    def preflight_remote(self) -> None:
        code, out = self._ssh(REMOTE_PREFLIGHT_SH, timeout=45)
        if code != 0:
            hint = self._ssh_failure_hint(code, out)
            self._add("vps-ssh", "FAIL", hint)
            return
        if "could not resolve hostname" in out or "Connection refused" in out or "Permission denied" in out:
            self._add("vps-ssh", "FAIL", " ".join(out.split())[:200])
            return
        self._add("vps-ssh", "PASS", f"connected to {self.host}")
        data = self._parse_remote_preflight(out)
        if not data:
            self._add("remote-preflight", "FAIL", "unparseable remote preflight output")
            return
        # python3
        ver_str = data.get("python", "none")
        try:
            parts = ver_str.split()[-1].split(".")
            ver = (int(parts[0]), int(parts[1]) if len(parts) > 1 else 0)  # type: tuple[int, int]
        except (ValueError, IndexError):
            ver = 0, 0
        if ver[0] > MIN_PYTHON[0] or (ver[0] == MIN_PYTHON[0] and ver[1] >= MIN_PYTHON[1]):
            self._add("remote-python3", "PASS", ver_str)
        else:
            self._add("remote-python3", "FAIL", f"python3 {ver_str} — need >= {MIN_PYTHON[0]}.{MIN_PYTHON[1]}")
        # disk
        try:
            disk_kb = int(data.get("disk_kb", "0"))
        except ValueError:
            disk_kb = 0
        if disk_kb * 1024 >= MIN_FREE_BYTES:
            self._add("remote-disk", "PASS", f"{disk_kb // 1024} MB free on /")
        else:
            self._add("remote-disk", "FAIL", f"only {disk_kb // 1024} MB free on /")
        # RAM
        try:
            mem_mb = int(data.get("mem_mb", "0"))
        except ValueError:
            mem_mb = 0
        if mem_mb >= MIN_REMOTE_MEM_MB:
            self._add("remote-mem", "PASS", f"{mem_mb} MB free RAM")
        else:
            self._add("remote-mem", "FAIL", f"only {mem_mb} MB free RAM (need >= {MIN_REMOTE_MEM_MB})")
        # home writable
        if data.get("home") == "WRITABLE":
            self._add("remote-home", "PASS", f"{self.remote_home} writable")
        else:
            self._add("remote-home", "FAIL", "remote home not writable for this user")
        # tar (needed for bundle extraction)
        if data.get("tar") != "NO_TAR":
            self._add("remote-tar", "PASS", "tar present")
        else:
            self._add("remote-tar", "FAIL", "tar not found on remote — install tar or use tar-less transport")

    def _ssh_failure_hint(self, code: int, out: str) -> str:
        low = out.lower()
        if code == 255 and ("permission denied" in low or "publickey" in low):
            return ("ssh auth failed — use your own ssh key (default ~/.ssh/id_* or "
                    "ssh-agent), or pass --ssh-key <path>. Deploy locally with --home if no VPS access.")
        if "could not resolve hostname" in low:
            return "unknown host — check --host spelling"
        if "connection refused" in low:
            return "connection refused — is the VPS reachable / sshd running?"
        return " ".join(out.split())[:200] or f"ssh exited {code}"

    # -- 2. upload + remote run ---------------------------------------------
    def _stage_remote(self) -> bool:
        self.staging = f"/tmp/teamops-vps-{secrets.token_hex(4)}"
        bundle = self._bundle()
        sq = shlex.quote(self.staging)
        # mkdir + untar the stream in one roundtrip
        code, out = self._ssh(
            f"mkdir -p {sq} && tar xzf - -C {sq}",
            stdin_bytes=bundle, timeout=180,
        )
        if code != 0:
            self._add("vps-upload", "FAIL", " ".join(out.split())[:200] or "bundle upload failed")
            return False
        self._add("vps-upload", "PASS", f"bundle staged at {self.staging}")
        return True

    def _run_remote(self, op: str) -> dict:
        """Execute the driver on the remote; returns parsed JSON summary.

        Every interpolated value (staging, remote home, blueprint name) is
        shlex.quote()d — the remote command is a shell string built from
        values that can be model-controlled (review round 2 #1).
        """
        sq = shlex.quote(self.staging or "")
        env = (
            f"PYTHONPATH={sq} "
            f"HERMES_HOME={shlex.quote(self.remote_home)} "
            f"TEAMOPS_NAME={shlex.quote(self.name)}"
        )
        code, out = self._ssh(
            f"cd {sq} && {env} python3 - {op}",
            stdin_bytes=self._driver(), timeout=300,
        )
        if code != 0:
            return {"success": False, "error": " ".join(out.split())[:250] or f"remote {op} failed (ssh {code})"}
        try:
            return json.loads(out.strip().splitlines()[-1] if out.strip() else "{}")
        except json.JSONDecodeError:
            return {"success": False, "error": f"unparseable remote output: {out[:200]!r}"}

    def _cleanup_staging(self) -> None:
        if self.staging:
            self._ssh(f"rm -rf {shlex.quote(self.staging or '')}", timeout=60)

    # -- 3. flows ------------------------------------------------------------
    def deploy(self, dry_run: bool = False, verify: bool = True) -> dict:
        ok, reason = transport_available(self.host)
        if not ok:
            self._add("vps-transport", "FAIL", reason)
            return VpsSummary(self.name, self.checks).to_dict(self.host)
        if dry_run:
            self._add("vps-dry-run", "PASS",
                      f"would preflight + bundle + deploy '{self.name}' to {self.host} "
                      f"(remote home {self.remote_home}, ssh key {self.key or 'agent/default'})")
            return VpsSummary(self.name, self.checks).to_dict(self.host)

        manager_checks = [CheckResult("vps-mode", "PASS", f"remote deploy via {self.host}")]
        self.preflight_remote()
        if any(c.status == "FAIL" for c in self.checks):
            return VpsSummary(self.name, manager_checks + self.checks).to_dict(self.host)

        if not self._stage_remote():
            return VpsSummary(self.name, manager_checks + self.checks).to_dict(self.host)

        remote = self._run_remote("deploy")
        remote_green = bool(remote.get("green", False)) and remote.get("success", False)
        self._add("remote-deploy", "PASS" if remote_green else "FAIL",
                  _remote_detail(remote))
        checks = manager_checks + self.checks

        if remote_green and verify:
            v = self._run_remote("verify")
            v_green = bool(v.get("green", False)) and v.get("success", False)
            self._add("remote-verify", "PASS" if v_green else "FAIL", _remote_detail(v))
            remote = {"deploy": remote, "verify": v}

        if remote_green:
            self._cleanup_staging()
        return VpsSummary(self.name, checks, remote=remote,
                          staging=self.staging if not remote_green else None).to_dict(self.host)

    def verify(self) -> dict:
        ok, reason = transport_available(self.host)
        if not ok:
            self._add("vps-transport", "FAIL", reason)
            return VpsSummary(self.name, self.checks).to_dict(self.host)
        manager_checks = [CheckResult("vps-mode", "PASS", f"remote verify via {self.host}")]
        if not self._stage_remote():
            return VpsSummary(self.name, manager_checks + self.checks).to_dict(self.host)
        remote = self._run_remote("verify")
        good = bool(remote.get("green", False)) and remote.get("success", False)
        self._add("remote-verify", "PASS" if good else "FAIL", _remote_detail(remote))
        checks = manager_checks + self.checks
        if good:
            self._cleanup_staging()
        return VpsSummary(self.name, checks, remote=remote,
                          staging=self.staging if not good else None).to_dict(self.host)

    def rollback(self) -> dict:
        ok, reason = transport_available(self.host)
        if not ok:
            self._add("vps-transport", "FAIL", reason)
            return VpsSummary(self.name, self.checks).to_dict(self.host)
        manager_checks = [CheckResult("vps-mode", "PASS", f"remote rollback via {self.host}")]
        if not self._stage_remote():
            return VpsSummary(self.name, manager_checks + self.checks).to_dict(self.host)
        remote = self._run_remote("rollback")
        good = bool(remote.get("success", False))
        self._add("remote-rollback", "PASS" if good else "FAIL", _remote_detail(remote))
        checks = manager_checks + self.checks
        if good:
            self._cleanup_staging()
        return VpsSummary(self.name, checks, remote=remote,
                          staging=self.staging if not good else None).to_dict(self.host)


def _remote_detail(remote: dict) -> str:
    if not remote:
        return "no remote summary"
    err = remote.get("error")
    if err:
        return err[:200]
    p, f, s = remote.get("pass", 0), remote.get("fail", 0), remote.get("skip", 0)
    return f"remote PASS {p} · FAIL {f} · SKIP {s} → {'GREEN' if remote.get('green') else 'NOT GREEN'}"
