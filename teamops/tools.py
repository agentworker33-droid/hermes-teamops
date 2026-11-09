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
                home: str | None = None, verify: bool = True) -> str:
    try:
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


def team_verify(name: str, home: str | None = None, blueprints_root: str | None = None) -> str:
    try:
        return _ok(build_mod.verify_team(name, home=Path(home) if home else None,
                                         blueprints_root=Path(blueprints_root) if blueprints_root else None))
    except Exception as e:  # noqa: BLE001
        return _err(f"team_verify failed: {e}")


def team_rollback(name: str, home: str | None = None, blueprints_root: str | None = None) -> str:
    try:
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


def model_scan(home: str | None = None) -> str:
    """Model Guard: dead/free-gone models + fallback recommendations."""
    try:
        from . import modelguard
        report = modelguard.scan(Path(home) if home else DEFAULT_HOME)
        return _ok(report)
    except Exception as e:  # noqa: BLE001
        return _err(f"model_scan failed: {e}")


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
