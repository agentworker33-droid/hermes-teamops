"""Deploy Guard: preflight / deploy / smoke / rollback for LOCAL team deploys.

Ported concepts from ~/validate-onboard.sh + ~/onboard-self-serve/scripts/check-deploy-live.sh
but generalized to any HERMES_HOME (no VPS, no systemd assumptions).

All mutations go through `hermes` CLI subprocesses with HERMES_HOME set to the
target home. Tests inject a fake runner via `DeployGuard.runner`.

Catalog-review safety rules:
- rollback touches ONLY profiles prefixed ``teamops-<team>-`` and ONLY files
  that resolve under ``<home>/teams/<team>/`` (snapshot entries outside those
  scopes are skipped, never deleted);
- deploy honours ``cron.allow_agent_scheduling`` in the target home config:
  when it is explicitly false, cron jobs are NOT created (SKIP rows explain);
- soul/shared paths from the blueprint MUST resolve inside the blueprint root.
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
TEAMOPS_PROFILE_PREFIX = "teamops-"


def _home_default() -> Path:
    """Default HERMES_HOME: hermes_constants when available, else env/~/hermes."""
    try:
        from hermes_constants import get_hermes_home
        return Path(get_hermes_home())
    except Exception:  # noqa: BLE001 — tests/dev runs
        return Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes"))


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


def _require_inside(root: Path, path: Path) -> Path:
    """Resolve ``path`` and require it stay inside ``root`` (review #5)."""
    root_r = root.resolve()
    p_r = (path if path.is_absolute() else root / path).resolve()
    if p_r == root_r or root_r in p_r.parents:
        return p_r
    raise ValueError(f"path escapes blueprint root {root}: {path}")


class DeployGuard:
    """Guard a single team deploy under a target HERMES_HOME."""

    runner = staticmethod(default_runner)

    def __init__(self, blueprint: Blueprint | None = None, home: Path | None = None, prefix: str | None = None,
                 team: str | None = None):
        self.blueprint = blueprint
        self.home = Path(home) if home else _home_default()
        self.team = team or (blueprint.name if blueprint else "team")
        # Prefixes always live in the teamops- namespace so rollback's scope check
        # (profiles start with teamops-<team>-) holds even for custom --prefix.
        self.prefix = prefix or f"teamops-{self.team}-"
        if not self.prefix.startswith(TEAMOPS_PROFILE_PREFIX):
            self.prefix = f"{TEAMOPS_PROFILE_PREFIX}{self.prefix}"
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

    def _bp(self) -> Blueprint:
        if self.blueprint is None:  # pragma: no cover — only reachable on programmer error
            raise RuntimeError("blueprint required for this operation")
        return self.blueprint

    def profile_name(self, role: dict) -> str:
        return f"{self.prefix}{role['name']}"

    def profile_dir(self, role: dict) -> Path:
        return self.home / "profiles" / self.profile_name(role)

    def team_shared_dir(self) -> Path:
        return self.home / "teams" / self.team

    def snapshot_path(self) -> Path:
        return self.team_shared_dir() / f".teamops-{self.team}.snapshot.json"

    def _allow_agent_scheduling(self) -> bool | None:
        """Read `cron.allow_agent_scheduling` from the target home config.yaml.
        None = unset (create crons; default behaviour for owner CLI deploys)."""
        cfg = self.home / "config.yaml"
        if not cfg.exists():
            return None
        try:
            import yaml
            doc = yaml.safe_load(cfg.read_text()) or {}
            return bool((doc.get("cron") or {}).get("allow_agent_scheduling"))
        except Exception:  # noqa: BLE001
            return None

    # -- 1. preflight --------------------------------------------------------
    def preflight(self) -> list[CheckResult]:
        self.checks = []
        self._add("blueprint", "PASS", f"{self.team} v{self._bp().team.get('version', '?')} · {len(self._bp().roles)} roles")
        binpath = shutil.which("hermes")
        if binpath:
            self._add("hermes-bin", "PASS", f"{binpath}")
        else:
            self._add("hermes-bin", "FAIL", "hermes CLI not found on PATH")
        for role in self._bp().roles:
            if not role.get("name") or not role.get("soul"):
                self._add("role-format", "FAIL", f"role {role.get('name')!r} missing name/soul")
                continue
            self._add("role-soul-path", "PASS", f"soul {role['soul']} OK")
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
        collisions = [self.profile_name(r) for r in self._bp().roles if self.profile_dir(r).exists()]
        if collisions:
            self._add("name-collision", "FAIL", f"profiles already exist: {', '.join(collisions)}")
        else:
            self._add("name-collision", "PASS", "no teamops profile collisions")
        # isolation: a target home strictly INSIDE the default hermes root is
        # treated by `hermes` as "profile mode" — profile create would resolve to
        # the native home and LEAK test profiles into the live box.
        try:
            default_root = _home_default().resolve()
            target = self.home.resolve()
            if target != default_root and default_root in target.parents:
                self._add("home-isolation", "FAIL",
                          f"target home {self.home} is inside the default hermes root "
                          f"{default_root}; hermes profile create would resolve to the native "
                          f"home. Use a directory OUTSIDE ~/.hermes for temp/test deploys.")
            else:
                self._add("home-isolation", "PASS", f"{self.home} resolves outside the default root")
        except OSError:
            self._add("home-isolation", "SKIP", "could not resolve home paths")
        # soul/shared confinement
        try:
            for key, rel in (self._bp().team.get("shared") or {}).items():
                _require_inside(self._bp().root, Path(rel))
            for role in self._bp().roles:
                if role.get("soul"):
                    _require_inside(self._bp().root, Path(role["soul"]))
            self._add("blueprint-paths", "PASS", "soul/shared paths confined to blueprint root")
        except ValueError as e:
            self._add("blueprint-paths", "FAIL", str(e))
        return self.checks

    # -- 2. deploy -----------------------------------------------------------
    def deploy(self, dry_run: bool = False, chat_probe: bool = False) -> list[CheckResult]:
        self.checks = []
        self.snapshot = {"team": self.team, "prefix": self.prefix, "blueprint_root": str(self._bp().root),
                         "profiles": [], "crons": [], "shared": []}
        if dry_run:
            for role in self._bp().roles:
                self._add("dry-run", "PASS", f"would create profile {self.profile_name(role)}")
            return self.checks

        allow_sched = self._allow_agent_scheduling()
        if allow_sched is False:
            self._add("cron-scheduling", "SKIP",
                      "cron.allow_agent_scheduling is false in target config.yaml — cron jobs not created "
                      "(set it true in <home>/config.yaml to deploy jobs)")

        # 2a. profiles + souls
        for role in self._bp().roles:
            name = self.profile_name(role)
            title = role.get("title", role["name"])
            code, out = self._cli(["profile", "create", name, "--no-alias", "--description", title])
            if code != 0:
                self._add(f"profile:{name}", "FAIL", out.strip()[:200])
                continue
            soul_src = self._bp().soul_path(role)
            soul_dst = self.profile_dir(role) / "SOUL.md"
            try:
                soul_abs = _require_inside(self._bp().root, soul_src) if soul_src else None
                if soul_abs and soul_abs.exists():
                    soul_dst.parent.mkdir(parents=True, exist_ok=True)
                    soul_dst.write_text(soul_abs.read_text())
                    self._add(f"profile:{name}", "PASS", "created + SOUL.md written")
                else:
                    self._add(f"profile:{name}", "WARN", "created, no soul file in blueprint")
            except OSError as e:  # noqa: BLE001
                self._add(f"profile:{name}", "FAIL", f"soul write error: {e}")
            self.snapshot["profiles"].append(name)

        # 2b. crons (per-profile store) — honoring cron.allow_agent_scheduling
        for cron in self._bp().crons:
            role_name = cron.get("profile") or "boss"
            profile = f"{self.prefix}{role_name}"
            job_name = cron.get("name", "teamops-job")
            schedule = cron.get("schedule", "0 9 * * *")
            prompt = cron.get("prompt", "")
            if allow_sched is False:
                self._add(f"cron:{job_name}", "SKIP", f"not created — agent scheduling disabled (cron:{job_name})")
                continue
            code, out = self._cli([
                "-p", profile, "cron", "create",
                "--name", job_name, "--deliver", "local",
                schedule, prompt,
            ])
            job_id = self._find_job_id(profile, job_name)
            self.snapshot["crons"].append({"profile": profile, "name": job_name, "job_id": job_id})
            if code != 0:
                self._add(f"cron:{job_name}", "FAIL", out.strip()[:200])
            else:
                self._add(f"cron:{job_name}", "PASS", f"@{schedule} on {profile}")

        # 2c. shared kanban + mesh
        shared_dir = self.team_shared_dir()
        try:
            shared_dir.mkdir(parents=True, exist_ok=True)
            for key, rel in self._bp().team.get("shared", {}).items():
                src_abs = _require_inside(self._bp().root, Path(rel))
                if src_abs.exists():
                    dst = shared_dir / src_abs.name
                    if dst.resolve().parent.resolve() != shared_dir.resolve():
                        self._add(f"shared:{src_abs.name}", "FAIL", f"destination escapes team dir: {dst}")
                        continue
                    dst.write_text(src_abs.read_text())
                    self.snapshot["shared"].append(str(dst))
                    self._add(f"shared:{src_abs.name}", "PASS", f"{dst}")
                else:
                    self._add(f"shared:{src_abs.name}", "FAIL", f"missing in blueprint: {rel}")
            snap = self.snapshot_path()
            snap.parent.mkdir(parents=True, exist_ok=True)
            snap.write_text(json.dumps(self.snapshot, indent=2))
            self._add("snapshot", "PASS", f"{snap}")
        except (OSError, ValueError) as e:  # noqa: BLE001
            self._add("shared", "FAIL", str(e))
        return self.checks

    def _find_job_id(self, profile: str, job_name: str) -> str:
        jf = self.home / "profiles" / profile / "cron" / "jobs.json"
        if jf.exists():
            try:
                doc = json.loads(jf.read_text())
                for job in doc.get("jobs", []):
                    if job.get("name") == job_name:
                        return job.get("id", "")
            except (json.JSONDecodeError, ValueError):
                pass
        return ""

    # -- 3. smoke (verify) ---------------------------------------------------
    def smoke(self, chat_probe: bool = False) -> list[CheckResult]:
        self.checks = []
        for role in self._bp().roles:
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
        expected = [c.get("name") for c in self._bp().crons if c.get("name")]
        found = self._find_job_names()
        for job in expected:
            if job in found:
                self._add(f"cron-present:{job}", "PASS", "in jobs.json")
            else:
                self._add(f"cron-present:{job}", "FAIL", f"not found in any jobs.json (found: {sorted(found) or 'none'})")
        # shared assets parse
        for key, rel in self._bp().team.get("shared", {}).items():
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
            role0 = self._bp().roles[0]
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
        for role in self._bp().roles:
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
        """Snapshot-driven rollback: undo everything a deploy created.

        SAFETY (catalog review #4): only profiles whose name starts with the
        snapshot's ``teamops-<team>-`` prefix are deleted, and only shared files
        that resolve under ``<home>/teams/<team>/`` are removed. Entries outside
        those scopes are skipped with a WARN — never deleted. Does NOT require
        the blueprint (fixtures may be gone); snapshot is the source of truth.
        """
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
        team = snap.get("team", self.team)
        prefix = snap.get("prefix", self.prefix)
        self.prefix = prefix
        self.team = team
        shared_dir = self.team_shared_dir().resolve()
        # crons first — they live inside the profile; once the profile is gone the
        # jobs die with it, so a missing profile here is already-clean, not a failure.
        for cron in snap.get("crons", []):
            prof = cron.get("profile")
            name = cron.get("name")
            job_id = cron.get("job_id")
            if not prof or not name:
                continue
            if not prof.startswith(prefix):
                self._add(f"rollback-cron:{name}", "WARN", f"profile {prof} outside team prefix — skipped")
                continue
            if not job_id:
                self._add(f"rollback-cron:{name}", "SKIP", "no job_id in snapshot (older deploy)")
                continue
            code, out = self._cli(["-p", prof, "cron", "remove", job_id], timeout=120)
            detail = " ".join((out or "").split())[:160] or "removed"
            if code == 0 or "does not exist" in out.lower():
                self._add(f"rollback-cron:{name}", "PASS" if code == 0 else "SKIP", detail)
            else:
                self._add(f"rollback-cron:{name}", "FAIL", detail)
        for prof in snap.get("profiles", []):
            if not prof.startswith(prefix) or not prof.startswith(TEAMOPS_PROFILE_PREFIX):
                self._add(f"rollback-profile:{prof}", "WARN", f"profile {prof} outside teamops-<team>- prefix — skipped")
                continue
            code, out = self._cli(["profile", "delete", "-y", prof], timeout=120)
            detail = " ".join((out or "").split())[:160] or "deleted"
            self._add(f"rollback-profile:{prof}", "PASS" if code == 0 else "FAIL", detail)
        for f in snap.get("shared", []):
            p = Path(f)
            try:
                p_res = p.resolve()
            except OSError:
                p_res = p.absolute()
            if shared_dir not in p_res.parents and p_res != shared_dir:
                self._add(f"rollback-file:{p.name}", "WARN", f"{p} outside <home>/teams/<team>/ — skipped")
                continue
            if p.exists():
                try:
                    p.unlink()
                    self._add(f"rollback-file:{p.name}", "PASS", "removed")
                except OSError as e:  # noqa: BLE001
                    self._add(f"rollback-file:{p.name}", "FAIL", str(e))
        try:
            snap_path.unlink()
            self._add("rollback-snapshot", "PASS", "snapshot removed")
        except OSError as e:  # noqa: BLE001
            self._add("rollback-snapshot", "WARN", str(e))
        if shared_dir.exists() and not any(shared_dir.iterdir()):
            try:
                shared_dir.rmdir()
                self._add("rollback-dir", "PASS", "team shared dir removed")
            except OSError as e:  # noqa: BLE001
                self._add("rollback-dir", "WARN", str(e))
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
