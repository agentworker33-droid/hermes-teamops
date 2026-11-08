"""Tool handlers for hermes-teamops.

D0: blueprint_list + team_status are functional. Remaining handlers are
honest stubs returning not-implemented errors — filled in across D1/D2 build
shifts (see WORKLOG.md).
"""
from __future__ import annotations

import json
import os
from pathlib import Path

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
        return _err(f"{name}: not implemented yet (D1/D2 build shift) — see WORKLOG.md")
    return handler


team_deploy = _stub("team_deploy")
team_verify = _stub("team_verify")
team_rollback = _stub("team_rollback")
team_factory = _stub("team_factory")
cron_scan = _stub("cron_scan")
model_scan = _stub("model_scan")
team_standup = _stub("team_standup")
mesh_sync = _stub("mesh_sync")
