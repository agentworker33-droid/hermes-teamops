"""Tool handlers for hermes-teamops.

Contract (Hermes plugin tool API): every handler is
``def handler(args: dict, **kwargs) -> str`` — it reads its fields from
``args`` (the dict the LLM passed), never raises, and always returns a JSON
string. CLI subcommands build that same dict and call the same handlers, so
the CLI and the tool surface stay in sync.

Path-like arguments (``home``, ``jobs_path``, ``db_path``, ``blueprints_root``,
``path``) are CLI-only, NOT exposed in the tool schemas (catalog review #6);
the default HERMES_HOME resolves through ``hermes_constants.get_hermes_home()``
with an env-var fallback for tests/dev runs outside the Hermes runtime.

OWNER ACTIONS (``team_deploy``, ``team_rollback``, ``team_verify`` with
``--host``, ``team_factory`` with ``deploy=true``, ``model_scan`` swap-apply,
``model_rollback``) are gated by a ``pre_tool_call`` hook registered in
``__init__.py``: the hook returns an ``approve`` directive so the call
escalates to the human-approval gate, and fails closed in cron/unattended
runs (catalog review #4 + round 2 #2). The handlers themselves also defend:
they refuse to run an owner action when a cron-worker marker is present.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from . import build as build_mod
from . import factory as factory_mod

DEFAULT_BLUEPRINTS = Path(__file__).resolve().parent / "blueprints"

# Owner tools also carry the CLI-side refusal when a cron worker marker is
# present (defense in depth behind the pre_tool_call approve directive).
OWNER_TOOLS = ("team_deploy", "team_rollback", "team_factory")


def default_home() -> Path:
    """Resolve the default HERMES_HOME via hermes_constants when available
    (Hermes runtime), falling back to env var / ~/.hermes for tests."""
    try:
        from hermes_constants import get_hermes_home
        return Path(get_hermes_home())
    except Exception:  # noqa: BLE001 — tests / dev runs without hermes_constants
        return Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes"))


def _params(args: dict | None, kwargs: dict,
            schema_keys: set[str], cli_only: set[str] | None = None) -> dict:
    """Merge Hermes-style args dict with explicit kwargs (CLI/tests).

    Review round 3 #1: schema keys are read from ``args`` ONLY — that is the
    dict the LLM is allowed to send. CLI-only keys (``home``, ``path``,
    ``out_path``, ``prefix``, ``jobs_path``, ``db_path``, ``verify``…) are
    accepted ONLY through ``kwargs``, which the CLI / tests / remote driver
    pass explicitly. A model call like ``team_status({"html": True,
    "out_path": "/etc/x"})`` or ``blueprint_list({"path": "/etc"})`` can
    therefore never reach an arbitrary path; unapproved read tools stay
    read-only against the plugin's own resolved home. ``kwargs`` may also
    carry schema keys (tests/dev convenience); both sets merge there.
    """
    schema_keys = set(schema_keys)
    cli_only = set(cli_only or ())
    merged = {k: v for k, v in (args or {}).items() if k in schema_keys}
    for k in schema_keys | cli_only:
        if k in kwargs and kwargs[k] is not None:
            merged[k] = kwargs[k]
    return merged


def _ok(data) -> str:
    return json.dumps({"success": True, **data}, ensure_ascii=False)


def _err(message: str) -> str:
    return json.dumps({"success": False, "error": message}, ensure_ascii=False)


def _refuse_owner_action() -> bool:
    """True when running inside a cron worker (no human to approve)."""
    return bool(os.environ.get("_HERMES_CRON_WORKER_BOOT")
                or os.environ.get("_HERMES_CRON_EXTERNAL_WORKER"))


def blueprint_list(args=None, **kwargs) -> str:
    p = _params(args, kwargs, set(), {"path"})
    root = Path(p["path"]) if p.get("path") else DEFAULT_BLUEPRINTS
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
            import yaml  # lazily; pyyaml is a declared dependency
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
        return default_home()
    p = Path(arg).expanduser()
    if p.is_dir():
        return p
    if p.is_file():
        return p.parent
    return p


def team_deploy(args=None, **kwargs) -> str:
    """Deploy a blueprint — locally by default, or to a VPS via ssh with --host.

    OWNER ACTION: gated by the pre_tool_call approve directive; also refuses
    to run inside a cron worker (no human to approve).
    """
    p = _params(args, kwargs, {"name", "prefix", "dry_run", "host", "ssh_key",
                               "remote_home"}, {"home", "verify"})
    name = p.get("name", "")
    if _refuse_owner_action():
        return _err("team_deploy is an OWNER ACTION — refused in a cron/unattended run. "
                    "Run it interactively ('hermes teams deploy ...') so a human approves.")
    try:
        home = Path(p["home"]) if p.get("home") else None
        if p.get("host"):
            from . import vps
            return _ok(vps.VpsDeploy(
                name, p["host"], key=p.get("ssh_key"), remote_home=p.get("remote_home"),
            ).deploy(dry_run=bool(p.get("dry_run")), verify=p.get("verify", True)))
        result = build_mod.deploy_team(
            name, home=home, prefix=p.get("prefix"), dry_run=bool(p.get("dry_run")),
            blueprints_root=None,
        )
        if not p.get("dry_run") and p.get("verify", True) and result.get("green"):
            vresult = build_mod.verify_team(name, home=home)
            result["verify"] = vresult
            result["green"] = vresult.get("green", False)
        return _ok(result)
    except Exception as e:  # noqa: BLE001
        return _err(f"team_deploy failed: {e}")


def team_verify(args=None, **kwargs) -> str:
    """Run Deploy Guard smoke checks on a deployed team.

    Local verify is read-only; with --host it becomes an owner action
    (stages the bundle + runs the driver on a REMOTE machine) — gated by the
    pre_tool_call hook and refused in cron/unattended runs (review round 2 #2).
    """
    p = _params(args, kwargs, {"name", "host", "ssh_key", "remote_home"}, {"home"})
    if p.get("host") and _refuse_owner_action():
        return _err("team_verify with --host is an OWNER ACTION — refused in a cron/unattended "
                    "run. Run it interactively ('hermes teams verify ... --host') so a human approves.")
    try:
        home = Path(p["home"]) if p.get("home") else None
        if p.get("host"):
            from . import vps
            return _ok(vps.VpsDeploy(
                p["name"], p["host"], key=p.get("ssh_key"), remote_home=p.get("remote_home"),
            ).verify())
        return _ok(build_mod.verify_team(p.get("name", ""), home=home))
    except Exception as e:  # noqa: BLE001
        return _err(f"team_verify failed: {e}")


def team_rollback(args=None, **kwargs) -> str:
    """OWNER ACTION: undo a deploy from its snapshot. Only teamops-prefixed
    profiles and files under <home>/teams/<team>/ are ever touched (guard)."""
    p = _params(args, kwargs, {"name", "host", "ssh_key", "remote_home"}, {"home"})
    if _refuse_owner_action():
        return _err("team_rollback is an OWNER ACTION — refused in a cron/unattended run. "
                    "Run it interactively ('hermes teams rollback ...') so a human approves.")
    try:
        home = Path(p["home"]) if p.get("home") else None
        if p.get("host"):
            from . import vps
            return _ok(vps.VpsDeploy(
                p["name"], p["host"], key=p.get("ssh_key"), remote_home=p.get("remote_home"),
            ).rollback())
        return _ok(build_mod.rollback_team(p.get("name", ""), home=home))
    except Exception as e:  # noqa: BLE001
        return _err(f"team_rollback failed: {e}")


def team_factory(args=None, **kwargs) -> str:
    """Generate a blueprint from a spec via the built-in template generator.

    deploy=true is an OWNER ACTION (approval required); the generator itself
    (template + fixture fallback) is safe to run anytime.
    """
    p = _params(args, kwargs, {"spec", "base", "name", "use_fixture", "deploy"},
                {"home"})
    if p.get("deploy") and _refuse_owner_action():
        return _err("team_factory deploy is an OWNER ACTION — refused in a cron/unattended "
                    "run. Run it interactively ('hermes teams factory ... --deploy').")
    try:
        home = Path(p["home"]) if p.get("home") else None
        root, meta = factory_mod.generate_blueprint(
            p.get("spec", ""), name=p.get("name"), base=p.get("base"),
            use_fixture=bool(p.get("use_fixture")), home=home)
        result = {"blueprint": root.name, "path": str(root), "source": meta.get("source"),
                  "note": meta.get("note", ""), "summary": meta.get("summary", "")}
        if p.get("deploy"):
            d = build_mod.deploy_team(root.name, home=home,
                                      blueprints_root=root.parent)
            result["deploy"] = d
            if d.get("green"):
                result["verify"] = build_mod.verify_team(root.name, home=home,
                                                         blueprints_root=root.parent)
        return _ok(result)
    except Exception as e:  # noqa: BLE001
        return _err(f"team_factory failed: {e}")


def team_status(args=None, **kwargs) -> str:
    """Fleet read: per-profile memory %, crons, executions, mesh edges.

    ``html=True`` writes a self-contained dashboard (fleet panel + interactive
    mesh graph) and returns its file path instead of the JSON report.
    """
    p = _params(args, kwargs, {"team", "html"}, {"out_path", "home"})
    try:
        from . import fleet
        home_p = Path(p["home"]) if p.get("home") else default_home()
        if p.get("html"):
            from . import dashboard
            return _ok(dashboard.write_dashboard(
                home_p, team_filter=p.get("team"),
                out_path=Path(p["out_path"]) if p.get("out_path") else None))
        report = fleet.fleet_report(home_p, team_filter=p.get("team"))
        return _ok(report)
    except Exception as e:  # noqa: BLE001
        return _err(f"team_status failed: {e}")


def cron_scan(args=None, **kwargs) -> str:
    """Cron911: silent gaps + failures + dead models (detect-only)."""
    p = _params(args, kwargs, set(), {"jobs_path", "db_path"})
    try:
        from . import cron911
        home = _resolve_home_arg(p.get("jobs_path"))
        db_override = Path(p["db_path"]) if p.get("db_path") else None
        report = cron911.scan(home, db_override=db_override)
        return _ok(report)
    except Exception as e:  # noqa: BLE001
        return _err(f"cron_scan failed: {e}")


def model_scan(args=None, **kwargs) -> str:
    """Model Guard: dead/free-gone models + fallback recommendations.

    ``swap=False`` (default) is detect + recommend only. ``swap=True`` is the
    OPT-IN auto-swap mode: dead pinned models and scope defaults are swapped to
    the first pool fallback. Dry-run by default; never runs without a pool.
    swap WITHOUT dry_run rewrites model configs — an OWNER ACTION gated by the
    pre_tool_call hook and refused in cron/unattended runs (review round 2 #2).
    """
    p = _params(args, kwargs, {"swap", "pool", "dry_run"}, {"home"})
    if p.get("swap") and not p.get("dry_run", True) and _refuse_owner_action():
        return _err("model_scan swap-apply is an OWNER ACTION — refused in a cron/unattended "
                    "run. Run it interactively ('hermes teams model-scan --swap --apply') so a "
                    "human approves.")
    try:
        from . import modelguard
        home_p = Path(p["home"]) if p.get("home") else default_home()
        if not p.get("swap"):
            return _ok(modelguard.scan(home_p))
        result = modelguard.swap(home_p, pool=p.get("pool"), dry_run=p.get("dry_run", True))
        if not result.get("ok"):
            return _err(result.get("error", "swap failed"))
        return _ok(result)
    except Exception as e:  # noqa: BLE001
        return _err(f"model_scan failed: {e}")


def model_rollback(args=None, **kwargs) -> str:
    """Undo model-guard swaps from the changelog (most recent, or --all).

    Rewrites model configs — OWNER ACTION (approval required), refused in
    cron/unattended runs (review round 2 #2).
    """
    p = _params(args, kwargs, {"all_swaps"}, {"home"})
    if _refuse_owner_action():
        return _err("model_rollback is an OWNER ACTION — refused in a cron/unattended run. "
                    "Run it interactively ('hermes teams model-rollback') so a human approves.")
    try:
        from . import modelguard
        home_p = Path(p["home"]) if p.get("home") else default_home()
        result = modelguard.rollback(home_p, all_swaps=bool(p.get("all_swaps")))
        if not result.get("ok"):
            return _err(result.get("error", "rollback failed"))
        return _ok(result)
    except Exception as e:  # noqa: BLE001
        return _err(f"model_rollback failed: {e}")


def team_standup(args=None, **kwargs) -> str:
    """Morning digest: last-Nh cron outcomes per profile + open kanban work."""
    p = _params(args, kwargs, {"team", "hours"}, {"home"})
    try:
        from . import standup
        home_p = Path(p["home"]) if p.get("home") else default_home()
        report = standup.digest(home_p, team=p.get("team"), hours=p.get("hours", 24))
        return _ok(report)
    except Exception as e:  # noqa: BLE001
        return _err(f"team_standup failed: {e}")


def mesh_sync(args=None, **kwargs) -> str:
    """Broadcast a decision to team MESH-MAP.md + shared Mnemosyne surface."""
    p = _params(args, kwargs, {"message", "author", "team"}, {"home"})
    try:
        from . import meshbridge
        home_p = Path(p["home"]) if p.get("home") else default_home()
        report = meshbridge.sync(home_p, p.get("message", ""), author=p.get("author", "teamops"),
                                 team=p.get("team"))
        return _ok(report)
    except Exception as e:  # noqa: BLE001
        return _err(f"mesh_sync failed: {e}")


def team_dashboard(args=None, **kwargs) -> str:
    """Start (or reuse) the LIVE dashboard server; data regenerates per request."""
    p = _params(args, kwargs, {"port", "open_browser"}, {"home"})
    try:
        from . import server
        home_p = _resolve_home_arg(p.get("home"))
        pid = server.running(home_p)
        if pid:
            return _ok({"url": f"http://127.0.0.1:{p.get('port', 8781)}/", "started": False,
                        "running": True, "pid": pid, "home": str(home_p),
                        "note": "live dashboard already running"})
        r = server.start_daemon(home_p, p.get("port", 8781), p.get("open_browser", True))
        if r.get("ok"):
            return _ok({"url": r.get("url"), "started": True, "running": True,
                        "pid": r.get("pid"), "home": str(home_p),
                        "note": "live dashboard — every open re-reads HERMES_HOME"})
        return _err(r.get("error", "start failed"))
    except Exception as e:  # noqa: BLE001
        return _err(f"team_dashboard failed: {e}")


def team_dashboard_status(args=None, **kwargs) -> str:
    p = _params(args, kwargs, set(), {"home"})
    try:
        from . import server
        return _ok(server.status(_resolve_home_arg(p.get("home"))))
    except Exception as e:  # noqa: BLE001
        return _err(f"team_dashboard_status failed: {e}")


def team_dashboard_stop(args=None, **kwargs) -> str:
    p = _params(args, kwargs, set(), {"home"})
    try:
        from . import server
        return _ok(server.stop(_resolve_home_arg(p.get("home"))))
    except Exception as e:  # noqa: BLE001
        return _err(f"team_dashboard_stop failed: {e}")


def phone_intake(args=None, **kwargs) -> str:
    """Twilio phone intake: inbound SMS/call -> structured card (OPTIONAL, mock-first).

    Credentials are NEVER stored by the plugin — they are read from the
    RUNNING USER's environment through Hermes' secret scope
    (``agent.secret_scope.get_secret``; declared ``optional_env`` in
    plugin.yaml). Without them the LIVE path is blocked and this returns a
    clean 'disabled' refusal; mock=True always runs.
    """
    p = _params(args, kwargs, {"payload", "channel", "from_number", "to_number",
                               "body", "transcript", "call_status", "mock",
                               "persist"}, {"home"})
    try:
        from . import twilio_intake as ti
        home_p = Path(p["home"]) if p.get("home") else default_home()
        data = dict(p.get("payload") or {})
        for key, val in (("From", p.get("from_number")), ("To", p.get("to_number")),
                         ("Body", p.get("body")), ("CallStatus", p.get("call_status")),
                         ("channel", p.get("channel"))):
            if val is not None:
                data.setdefault(key, val)
        result = ti.handle_inbound(data, home=home_p, mock=bool(p.get("mock")),
                                   transcript=p.get("transcript"),
                                   persist=p.get("persist", True))
        if not result.get("success"):
            return _err(result.get("error", "twilio intake disabled"))
        return _ok(result)
    except Exception as e:  # noqa: BLE001
        return _err(f"phone_intake failed: {e}")


def twilio_status(args=None, **kwargs) -> str:
    """Is Twilio intake configured? (reads YOUR env via Hermes' secret scope;
    outputs masked values only — zero nags when disabled.)"""
    p = _params(args, kwargs, set(), {"home"})
    try:
        from . import twilio_intake as ti
        home_p = Path(p["home"]) if p.get("home") else default_home()
        return _ok(ti.status(home_p))
    except Exception as e:  # noqa: BLE001
        return _err(f"twilio_status failed: {e}")
