"""Tool handlers for hermes-teamops.

D1: Build module live — team_deploy / team_verify / team_rollback / team_factory
call build.* + factory.*. Watch/Fix/Grow handlers land in D2.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from . import build as build_mod
from . import factory as factory_mod

DEFAULT_BLUEPRINTS = Path(__file__).resolve().parent / "blueprints"
DEFAULT_HOME = Path(os.environ.get("HERMES_HOME", os.path.expanduser("~/.hermes")))


def _ok(data) -> str:
    return json.dumps({"success": True, **data}, ensure_ascii=False)


def _err(message: str) -> str:
    return json.dumps({"success": False, "error": message}, ensure_ascii=False)


def blueprint_list(path: str | None = None) -> str:
    root = Path(path) if path else DEFAULT_BLUEPRINTS
    if not root.is_dir():
        return _err(f"blueprint dir not found: {root}")
    out = []
    for d in sorted(root.iterdir()):
        if not d.is_dir():
            continue
        if d.name == "fixtures":
            continue
        meta = {}
        try:
            import yaml  # lazily; yaml is present in Hermes envs
            team = d / "team.yaml"
            if team.exists():
                doc = yaml.safe_load(team.read_text()) or {}
                meta = {"name": doc.get("name"), "version": doc.get("version"),
                        "roles": [r.get("name") for r in (doc.get("roles") or [])]}
        except Exception as e:  # noqa: BLE001
            meta = {"parse_error": str(e)}
        out.append({"id": d.name, **meta})
    return _ok({"blueprints": out, "count": len(out)})


def _resolve_home_arg(arg: str | None) -> Path:
    """Resolve a --home/jobs_path CLI arg: a directory is a HERMES_HOME,
    a file is treated as jobs.json (its parent is the home)."""
    if not arg:
        return DEFAULT_HOME
    p = Path(arg).expanduser()
    if p.is_dir():
        return p
    if p.is_file():
        return p.parent
    return p


def _resolve_blueprint_root(path: str | None) -> Path:
    if path:
        p = Path(path).expanduser()
        if p.is_dir():
            return p
    return DEFAULT_BLUEPRINTS


def team_deploy(name: str, prefix: str | None = None, dry_run: bool = False,
                home: str | None = None, verify: bool = True,
                host: str | None = None, ssh_key: str | None = None,
                remote_home: str | None = None) -> str:
    """Deploy a blueprint — locally by default, or to a VPS via ssh with --host.

    ``host`` is the OPTIONAL SSH transport (Task 2.11): ``user@host[:port]``.
    Credentials come from the RUNNING USER's own ssh setup (agent / ~/.ssh or
    ``ssh_key``); the plugin never stores or ships credentials. Missing ssh or
    failed auth degrades to a clean FAIL check — local deploys are untouched.
    """
    try:
        if host:
            from . import vps
            return _ok(vps.VpsDeploy(
                name, host, key=ssh_key, remote_home=remote_home,
            ).deploy(dry_run=dry_run, verify=verify))
        result = build_mod.deploy_team(
            name, home=Path(home) if home else None, prefix=prefix, dry_run=dry_run,
            blueprints_root=_resolve_blueprint_root(None),
        )
        if not dry_run and verify and result.get("green"):
            vresult = build_mod.verify_team(name, home=Path(home) if home else None)
            result["verify"] = vresult
            result["green"] = vresult.get("green", False)
        return _ok(result)
    except Exception as e:  # noqa: BLE001
        return _err(f"team_deploy failed: {e}")


def team_verify(name: str, home: str | None = None, blueprints_root: str | None = None,
                host: str | None = None, ssh_key: str | None = None,
                remote_home: str | None = None) -> str:
    try:
        if host:
            from . import vps
            return _ok(vps.VpsDeploy(
                name, host, key=ssh_key, remote_home=remote_home,
            ).verify())
        return _ok(build_mod.verify_team(name, home=Path(home) if home else None,
                                         blueprints_root=Path(blueprints_root) if blueprints_root else None))
    except Exception as e:  # noqa: BLE001
        return _err(f"team_verify failed: {e}")


def team_rollback(name: str, home: str | None = None, blueprints_root: str | None = None,
                  host: str | None = None, ssh_key: str | None = None,
                  remote_home: str | None = None) -> str:
    try:
        if host:
            from . import vps
            return _ok(vps.VpsDeploy(
                name, host, key=ssh_key, remote_home=remote_home,
            ).rollback())
        return _ok(build_mod.rollback_team(name, home=Path(home) if home else None,
                                           blueprints_root=Path(blueprints_root) if blueprints_root else None))
    except Exception as e:  # noqa: BLE001
        return _err(f"team_rollback failed: {e}")


def team_factory(spec: str, base: str | None = None, name: str | None = None,
                 use_fixture: bool = False, deploy: bool = False,
                 home: str | None = None) -> str:
    try:
        if base:
            spec = f"{spec} (extend the '{base}' blueprint)"
        root, meta = factory_mod.generate_blueprint(spec, name=name, use_fixture=use_fixture)
        result = {"blueprint": root.name, "path": str(root), "source": meta.get("source"),
                  "note": meta.get("note", ""), "summary": meta.get("summary", "")}
        if deploy:
            d = build_mod.deploy_team(root.name, home=Path(home) if home else None,
                                      blueprints_root=root.parent)
            result["deploy"] = d
            if d.get("green"):
                result["verify"] = build_mod.verify_team(root.name, home=Path(home) if home else None,
                                                         blueprints_root=root.parent)
        return _ok(result)
    except Exception as e:  # noqa: BLE001
        return _err(f"team_factory failed: {e}")


def team_status(team: str | None = None, home: str | None = None,
                html: bool = False, out_path: str | None = None) -> str:
    """Fleet read: per-profile memory %, crons, executions, mesh edges.

    ``html=True`` writes a self-contained dashboard (fleet panel + interactive
    mesh graph) and returns its file path instead of the JSON report.
    """
    try:
        from . import fleet
        home_p = Path(home) if home else DEFAULT_HOME
        if html:
            from . import dashboard
            return _ok(dashboard.write_dashboard(
                home_p, team_filter=team,
                out_path=Path(out_path) if out_path else None))
        report = fleet.fleet_report(home_p, team_filter=team)
        return _ok(report)
    except Exception as e:  # noqa: BLE001
        return _err(f"team_status failed: {e}")


def cron_scan(jobs_path: str | None = None, db_path: str | None = None) -> str:
    """Cron911: silent gaps + failures + dead models (detect-only)."""
    try:
        from . import cron911
        home = _resolve_home_arg(jobs_path)
        db_override = Path(db_path) if db_path else None
        report = cron911.scan(home, db_override=db_override)
        return _ok(report)
    except Exception as e:  # noqa: BLE001
        return _err(f"cron_scan failed: {e}")


def model_scan(home: str | None = None, swap: bool = False, pool: list[str] | None = None,
               dry_run: bool = True) -> str:
    """Model Guard: dead/free-gone models + fallback recommendations.

    ``swap=False`` (default) is detect + recommend only. ``swap=True`` is the
    OPT-IN auto-swap mode: with a fallback pool (config key
    ``modelguard.fallback_pool`` in <home>/config/teamops.yaml, or the
    ``pool`` argument) dead pinned models and scope defaults are swapped to the
    first pool fallback. Dry-run by default; set ``dry_run=False`` to apply.
    Never runs without a pool (refuses cleanly, feature disabled by design).
    """
    try:
        from . import modelguard
        home_p = Path(home) if home else DEFAULT_HOME
        if not swap:
            return _ok(modelguard.scan(home_p))
        result = modelguard.swap(home_p, pool=pool, dry_run=dry_run)
        if not result.get("ok"):
            return _err(result.get("error", "swap failed"))
        return _ok(result)
    except Exception as e:  # noqa: BLE001
        return _err(f"model_scan failed: {e}")


def model_rollback(home: str | None = None, all_swaps: bool = False) -> str:
    """Undo model-guard swaps from the changelog (most recent, or --all)."""
    try:
        from . import modelguard
        result = modelguard.rollback(Path(home) if home else DEFAULT_HOME, all_swaps=all_swaps)
        if not result.get("ok"):
            return _err(result.get("error", "rollback failed"))
        return _ok(result)
    except Exception as e:  # noqa: BLE001
        return _err(f"model_rollback failed: {e}")


def team_standup(team: str | None = None, hours: int = 24, home: str | None = None) -> str:
    """Morning digest: last-Nh cron outcomes per profile + open kanban work."""
    try:
        from . import standup
        report = standup.digest(Path(home) if home else DEFAULT_HOME, team=team, hours=hours)
        return _ok(report)
    except Exception as e:  # noqa: BLE001
        return _err(f"team_standup failed: {e}")


def mesh_sync(message: str, author: str = "teamops", team: str | None = None, home: str | None = None) -> str:
    """Broadcast a decision to team MESH-MAP.md + shared Mnemosyne surface."""
    try:
        from . import meshbridge
        report = meshbridge.sync(Path(home) if home else DEFAULT_HOME, message, author=author, team=team)
        return _ok(report)
    except Exception as e:  # noqa: BLE001
        return _err(f"mesh_sync failed: {e}")


def team_dashboard(home: str | None = None, port: int = 8781, open_browser: bool = True) -> str:
    """Start (or reuse) the LIVE dashboard server; data regenerates per request."""
    try:
        from . import server
        home_p = _resolve_home_arg(home)
        pid = server.running(home_p)
        if pid:
            return _ok({"url": f"http://127.0.0.1:{port}/", "started": False,
                        "running": True, "pid": pid, "home": str(home_p),
                        "note": "live dashboard already running"})
        r = server.start_daemon(home_p, port, open_browser)
        if r.get("ok"):
            return _ok({"url": r.get("url"), "started": True, "running": True,
                        "pid": r.get("pid"), "home": str(home_p),
                        "note": "live dashboard — every open re-reads HERMES_HOME"})
        return _err(r.get("error", "start failed"))
    except Exception as e:  # noqa: BLE001
        return _err(f"team_dashboard failed: {e}")


def team_dashboard_status(home: str | None = None) -> str:
    try:
        from . import server
        return _ok(server.status(_resolve_home_arg(home)))
    except Exception as e:  # noqa: BLE001
        return _err(f"team_dashboard_status failed: {e}")


def team_dashboard_stop(home: str | None = None) -> str:
    try:
        from . import server
        return _ok(server.stop(_resolve_home_arg(home)))
    except Exception as e:  # noqa: BLE001
        return _err(f"team_dashboard_stop failed: {e}")
