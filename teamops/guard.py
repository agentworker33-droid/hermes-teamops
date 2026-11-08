"""Deploy Guard: preflight / deploy / smoke / rollback for LOCAL team deploys.

Ported concepts from ~/validate-onboard.sh + ~/onboard-self-serve/scripts/check-deploy-live.sh
but generalized to any HERMES_HOME (no VPS, no systemd assumptions).

All mutations go through `hermes` CLI subprocesses with HERMES_HOME set to the
target home. Tests inject a fake runner via `DeployGuard.runner`.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .build import Blueprint

MIN_FREE_BYTES = 200 * 1024 * 1024  # 200 MB


@dataclass
class CheckResult:
    name: str
    status: str  # PASS | FAIL | SKIP
    detail: str = ""
    extra: dict = field(default_factory=dict)


def default_runner(cmd: list[str], cwd: Path | None = None, timeout: int = 120, env: dict | None = None) -> tuple[int, str]:
    """Run a hermes CLI command. Returns (exit_code, stdout+stderr)."""
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, cwd=str(cwd) if cwd else None,
            env=env,
        )
        out = (proc.stdout or "") + (proc.stderr or "")
        return proc.returncode, out
    except subprocess.TimeoutExpired:
        return 124, "TIMEOUT"
    except FileNotFoundError:
        return 127, f"command not found: {cmd[0]}"


class DeployGuard:
    """Guard a single team deploy under a target HERMES_HOME."""

    runner = staticmethod(default_runner)

    def __init__(self, blueprint: Blueprint, home: Path | None = None, prefix: str | None = None):
        self.blueprint = blueprint
        self.home = Path(home) if home else Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes"))
        self.team = blueprint.name
        self.prefix = prefix or f"teamops-{self.team}-"
        self.checks: list[CheckResult] = []
        self.snapshot: dict = {}

    # -- helpers ------------------------------------------------------------
    def _cli_env(self) -> dict:
        env = dict(os.environ)
        env["HERMES_HOME"] = str(self.home)
        return env

    def _cli(self, args: list[str], timeout: int = 180) -> tuple[int, str]:
        env = self._cli_env()
        if self.runner is default_runner:
            return self.runner(["hermes", *args], cwd=None, timeout=timeout, env=env)
        try:
            return self.runner(["hermes", *args], timeout=timeout, env=env)
        except TypeError:  # old fake runners in tests without env param
            return self.runner(["hermes", *args], timeout=timeout)

    def _add(self, name: str, status: str, detail: str = "", **extra) -> None:
        self.checks.append(CheckResult(name, status, detail, extra))

    def profile_name(self, role: dict) -> str:
        return f"{self.prefix}{role['name']}"

    def profile_dir(self, role: dict) -> Path:
        return self.home / "profiles" / self.profile_name(role)

    def team_shared_dir(self) -> Path:
        return self.home / "teams" / self.team

    def snapshot_path(self) -> Path:
        return self.team_shared_dir() / f".teamops-{self.team}.snapshot.json"

    # -- 1. preflight --------------------------------------------------------
    def preflight(self) -> list[CheckResult]:
        self.checks = []
        self._add("blueprint", "PASS", f"{self.team} v{self.blueprint.team.get('version', '?')} · {len(self.blueprint.roles)} roles")
        binpath = shutil.which("hermes")
        if binpath:
            self._add("hermes-bin", "PASS", f"{binpath}")
        else:
            self._add("hermes-bin", "FAIL", "hermes CLI not found on PATH")
        for role in self.blueprint.roles:
            if not role.get("name") or not role.get("soul"):
                self._add("role-format", "FAIL", f"role {role.get('name')!r} missing name/soul")
                continue
        try:
            self.home.mkdir(parents=True, exist_ok=True)
            probe = self.home / ".teamops-write-probe"
            probe.write_text("ok")
            probe.unlink()
        except OSError as e:  # noqa: BLE001
            self._add("home-writable", "FAIL", f"{self.home}: {e}")
        st = shutil.disk_usage(self.home if self.home.exists() else Path("/"))
        if st.free > MIN_FREE_BYTES:
            self._add("disk", "PASS", f"{st.free // (1024*1024)} MB free")
        else:
            self._add("disk", "FAIL", f"only {st.free // (1024*1024)} MB free")
        # name collision
        collisions = [self.profile_name(r) for r in self.blueprint.roles if self.profile_dir(r).exists()]
        if collisions:
            self._add("name-collision", "FAIL", f"profiles already exist: {', '.join(collisions)}")
        else:
            self._add("name-collision", "PASS", "no teamops profile collisions")
        return self.checks

    # -- 2. deploy -----------------------------------------------------------
    def deploy(self, dry_run: bool = False, chat_probe: bool = False) -> list[CheckResult]:
        self.checks = []
        self.snapshot = {"team": self.team, "prefix": self.prefix, "profiles": [], "crons": [], "shared": []}
        if dry_run:
            for role in self.blueprint.roles:
                self._add("dry-run", "PASS", f"would create profile {self.profile_name(role)}")
            return self.checks

        # 2a. profiles + souls
        for role in self.blueprint.roles:
            name = self.profile_name(role)
            title = role.get("title", role["name"])
            code, out = self._cli(["profile", "create", name, "--no-alias", "--description", title])
            if code != 0:
                self._add(f"profile:{name}", "FAIL", out.strip()[:200])
                continue
            soul_src = self.blueprint.soul_path(role)
            soul_dst = self.profile_dir(role) / "SOUL.md"
            try:
                if soul_src and soul_src.exists():
                    soul_dst.parent.mkdir(parents=True, exist_ok=True)
                    soul_dst.write_text(soul_src.read_text())
                    self._add(f"profile:{name}", "PASS", "created + SOUL.md written")
                else:
                    self._add(f"profile:{name}", "WARN", "created, no soul file in blueprint")
            except OSError as e:  # noqa: BLE001
                self._add(f"profile:{name}", "FAIL", f"soul write error: {e}")
            self.snapshot["profiles"].append(name)

        # 2b. crons (per-profile store)
        for cron in self.blueprint.crons:
            role_name = cron.get("profile") or "boss"
            profile = f"{self.prefix}{role_name}"
            job_name = cron.get("name", "teamops-job")
            schedule = cron.get("schedule", "0 9 * * *")
            prompt = cron.get("prompt", "")
            code, out = self._cli([
                "-p", profile, "cron", "create",
                "--name", job_name, "--deliver", "local",
                schedule, prompt,
            ])
            self.snapshot["crons"].append({"profile": profile, "name": job_name})
            if code != 0:
                self._add(f"cron:{job_name}", "FAIL", out.strip()[:200])
            else:
                self._add(f"cron:{job_name}", "PASS", f"@{schedule} on {profile}")

        # 2c. shared kanban + mesh
        shared_dir = self.team_shared_dir()
        try:
            shared_dir.mkdir(parents=True, exist_ok=True)
            for key, rel in self.blueprint.team.get("shared", {}).items():
                src = self.blueprint.root / rel
                if src.exists():
                    dst = shared_dir / src.name
                    dst.write_text(src.read_text())
                    self.snapshot["shared"].append(str(dst))
                    self._add(f"shared:{src.name}", "PASS", f"{dst}")
                else:
                    self._add(f"shared:{src.name}", "FAIL", f"missing in blueprint: {rel}")
            snap = self.snapshot_path()
            snap.parent.mkdir(parents=True, exist_ok=True)
            snap.write_text(json.dumps(self.snapshot, indent=2))
            self._add("snapshot", "PASS", f"{snap}")
        except OSError as e:  # noqa: BLE001
            self._add("shared", "FAIL", str(e))
        return self.checks

    # -- 3. smoke (verify) ---------------------------------------------------
    def smoke(self, chat_probe: bool = False) -> list[CheckResult]:
        self.checks = []
        for role in self.blueprint.roles:
            name = self.profile_name(role)
            pdir = self.profile_dir(role)
            if pdir.is_dir():
                self._add(f"profile:{name}", "PASS", "dir exists")
                if (pdir / "SOUL.md").exists():
                    self._add(f"soul:{name}", "PASS", "SOUL.md present")
                else:
                    self._add(f"soul:{name}", "FAIL", "SOUL.md missing")
                if (pdir / "cron" / "jobs.json").exists():
                    self._add(f"cron-store:{name}", "PASS", "cron/jobs.json present")
                else:
                    self._add(f"cron-store:{name}", "SKIP", "no cron store (no jobs assigned)")
            else:
                self._add(f"profile:{name}", "FAIL", f"profile dir missing: {pdir}")
        # expected cron names present somewhere
        expected = [c.get("name") for c in self.blueprint.crons if c.get("name")]
        found = self._find_job_names()
        for job in expected:
            if job in found:
                self._add(f"cron-present:{job}", "PASS", "in jobs.json")
            else:
                self._add(f"cron-present:{job}", "FAIL", f"not found in any jobs.json (found: {sorted(found) or 'none'})")
        # shared assets parse
        for key, rel in self.blueprint.team.get("shared", {}).items():
            dst = self.team_shared_dir() / Path(rel).name
            if dst.exists():
                try:
                    json.loads(dst.read_text())
                    self._add(f"shared:{dst.name}", "PASS", "parses")
                except (json.JSONDecodeError, ValueError):
                    self._add(f"shared:{dst.name}", "FAIL", "invalid JSON")
            else:
                self._add(f"shared:{dst.name}", "FAIL", "missing")
        # chat probe (bounded, optional)
        if chat_probe:
            role0 = self.blueprint.roles[0]
            name = self.profile_name(role0)
            code, out = self._cli(["-p", name, "chat", "-q", "reply: ok"], timeout=60)
            if code == 0 and "ok" in out.lower():
                self._add(f"chat-probe:{name}", "PASS", "agent replied")
            elif "no api keys" in out.lower() or "credential" in out.lower():
                self._add(f"chat-probe:{name}", "SKIP", "no provider credentials in temp home")
            else:
                self._add(f"chat-probe:{name}", "FAIL", out.strip()[:200])
        return self.checks

    def _find_job_names(self) -> set[str]:
        found: set[str] = set()
        for role in self.blueprint.roles:
            jf = self.profile_dir(role) / "cron" / "jobs.json"
            if jf.exists():
                try:
                    doc = json.loads(jf.read_text())
                    for job in doc.get("jobs", []):
                        found.add(job.get("name", ""))
                except (json.JSONDecodeError, ValueError):
                    continue
        return {f for f in found if f}

    # -- 4. rollback -----------------------------------------------------------
    def rollback(self) -> list[CheckResult]:
        self.checks = []
        snap_path = self.snapshot_path()
        if not snap_path.exists():
            self._add("rollback", "SKIP", f"no snapshot at {snap_path} — nothing to undo")
            return self.checks
        try:
            snap = json.loads(snap_path.read_text())
        except (json.JSONDecodeError, ValueError) as e:  # noqa: BLE001
            self._add("rollback", "FAIL", f"snapshot unreadable: {e}")
            return self.checks
        for prof in snap.get("profiles", []):
            code, out = self._cli(["profile", "delete", "-y", prof], timeout=120)
            self._add(f"rollback-profile:{prof}", "PASS" if code == 0 else "FAIL", out.strip()[:200] or "deleted")
        for f in snap.get("shared", []):
            p = Path(f)
            if p.exists():
                try:
                    p.unlink()
                    self._add(f"rollback-file:{p.name}", "PASS", "removed")
                except OSError as e:  # noqa: BLE001
                    self._add(f"rollback-file:{p.name}", "FAIL", str(e))
        shared_dir = self.team_shared_dir()
        if shared_dir.exists() and not any(shared_dir.iterdir()):
            try:
                shared_dir.rmdir()
                self._add("rollback-dir", "PASS", "team shared dir removed")
            except OSError as e:  # noqa: BLE001
                self._add("rollback-dir", "WARN", str(e))
        try:
            snap_path.unlink()
            self._add("rollback-snapshot", "PASS", "snapshot removed")
        except OSError as e:  # noqa: BLE001
            self._add("rollback-snapshot", "WARN", str(e))
        return self.checks

    # -- reporting --------------------------------------------------------------
    def summary(self) -> dict:
        return {
            "team": self.team,
            "checks": [c.__dict__ for c in self.checks],
            "pass": sum(1 for c in self.checks if c.status == "PASS"),
            "fail": sum(1 for c in self.checks if c.status == "FAIL"),
            "skip": sum(1 for c in self.checks if c.status in ("SKIP", "WARN")),
            "green": all(c.status != "FAIL" for c in self.checks),
        }

    def render_table(self) -> str:
        lines = [f"Deploy Guard — {self.team}", "-" * 60]
        for c in self.checks:
            lines.append(f"  {c.status:<5} {c.name:<28} {c.detail}")
        s = self.summary()
        lines.append("-" * 60)
        lines.append(f"  PASS {s['pass']} · FAIL {s['fail']} · SKIP {s['skip']} → {'GREEN' if s['green'] else 'NOT GREEN'}")
        return "\n".join(lines)
