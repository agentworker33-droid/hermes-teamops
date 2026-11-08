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

DEFAULT_BLUEPRINTS = Path(__file__).resolve().parent.parent / "blueprints"
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


def team_verify(name: str, home: str | None = None) -> str:
    try:
        return _ok(build_mod.verify_team(name, home=Path(home) if home else None))
    except Exception as e:  # noqa: BLE001
        return _err(f"team_verify failed: {e}")


def team_rollback(name: str, home: str | None = None) -> str:
    try:
        return _ok(build_mod.rollback_team(name, home=Path(home) if home else None))
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
                result["verify"] = build_mod.verify_team(root.name, home=Path(home) if home else None)
        return _ok(result)
    except Exception as e:  # noqa: BLE001
        return _err(f"team_factory failed: {e}")


def team_status(team: str | None = None) -> str:
    """Fleet read: enumerate profiles under HERMES_HOME/profiles."""
    profiles_dir = DEFAULT_HOME / "profiles"
    profiles = []
    if profiles_dir.is_dir():
        for p in sorted(profiles_dir.iterdir()):
            if p.is_dir():
                if team and team not in p.name:
                    continue
                cfg = p / "config.yaml"
                profiles.append({"name": p.name, "has_config": cfg.exists()})
    return _ok({"profiles": profiles, "count": len(profiles)})


def _stub(name: str):
    def handler(**_) -> str:
        return _err(f"{name}: not implemented yet (D2 build shift) — see WORKLOG.md")
    return handler


cron_scan = _stub("cron_scan")
model_scan = _stub("model_scan")
team_standup = _stub("team_standup")
mesh_sync = _stub("mesh_sync")
