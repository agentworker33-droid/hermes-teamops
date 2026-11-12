"""TeamOps: build, run, watch, fix and grow your Hermes agent team."""
from __future__ import annotations

import json
from pathlib import Path

from . import schemas, tools

# OWNER ACTIONS (catalog review #4 + round 2 #2): these tools create/delete
# profiles, cron jobs, remote state, or rewrite model configs, so the
# pre_tool_call hook escalates them to the human-approval gate with an
# approve directive. In cron/unattended runs the gate fails closed (no
# human), and the handlers refuse explicitly too.
_OWNER_GATED = {"team_deploy", "team_rollback", "model_rollback"}
# team_verify is read-only locally, but with --host it stages + runs the
# driver REMOTELY (writes on the target) — gate it only in that case.
_VERIFY_HOST_GATED = "team_verify"
# model_scan is detect-only by default; swap WITHOUT dry_run writes model
# configs — gate only the applying swap call.
_SWAP_APPLY_GATED = "model_scan"
_FACTORY_DEPLOY_GATED = "team_factory"


def _on_pre_tool_call(tool_name: str = "", args: dict | None = None, **kwargs):
    """Gate owner actions: approve directive -> human approval required.

    ``team_deploy`` / ``team_rollback`` / ``model_rollback`` always;
    ``team_verify`` only when ``host`` is set (remote writes);
    ``model_scan`` only when ``swap`` is on AND ``dry_run`` is off (apply);
    ``team_factory`` only when the call requests ``deploy=true``.
    Generation/scan-only calls are safe and never gated.
    """
    if tool_name in _OWNER_GATED:
        return {
            "action": "approve",
            "message": "TeamOps owner action: creates/deletes Hermes profiles, cron jobs "
                       "and team files%s. Approve to continue, or run 'hermes teams "
                       f"{'rollback' if tool_name == 'team_rollback' else 'deploy'}' from the CLI."
                       % (" (model_rollback reverts model-guard swaps)" if tool_name == "model_rollback" else ""),
            "rule_key": f"teamops.{tool_name}",
        }
    if tool_name == _VERIFY_HOST_GATED:
        if isinstance(args, dict) and args.get("host"):
            return {
                "action": "approve",
                "message": "TeamOps owner action: team_verify with --host stages the plugin "
                           "bundle and runs Deploy Guard on a REMOTE machine. Approve to "
                           "continue, or run 'hermes teams verify ... --host' from the CLI.",
                "rule_key": "teamops.team_verify.host",
            }
    if tool_name == _SWAP_APPLY_GATED:
        if isinstance(args, dict) and args.get("swap") and not args.get("dry_run", True):
            return {
                "action": "approve",
                "message": "TeamOps owner action: model_scan with swap + dry_run=false REWRITES "
                           "model configs (jobs.json / config.yaml model.default) from the "
                           "fallback pool. Approve to continue, or run 'hermes teams model-scan "
                           "--swap --apply' from the CLI.",
                "rule_key": "teamops.model_scan.swap_apply",
            }
    if tool_name == _FACTORY_DEPLOY_GATED:
        deploy = bool((args or {}).get("deploy")) if isinstance(args, dict) else False
        if deploy:
            return {
                "action": "approve",
                "message": "TeamOps owner action: team_factory with deploy=true creates "
                           "profiles and cron jobs. Approve to continue, or run "
                           "'hermes teams factory ... --deploy' from the CLI.",
                "rule_key": "teamops.team_factory.deploy",
            }
    return None


def register(ctx) -> None:
    """Register every tool declared in schemas.SCHEMAS + the `hermes teams` CLI."""
    for name in schemas.SCHEMAS:
        handler = getattr(tools, name, None)
        if handler is None:
            continue
        spec = schemas.SCHEMAS[name]
        ctx.register_tool(
            name=name,
            toolset=spec.get("toolset", "teamops"),
            schema=spec["schema"],
            handler=handler,
        )
    try:
        ctx.register_hook("pre_tool_call", _on_pre_tool_call)
    except Exception:  # noqa: BLE001 — hook registration must not break tools
        pass
    try:
        skill_path = Path(__file__).resolve().parent / "skills" / "teamops" / "SKILL.md"
        if skill_path.exists():
            ctx.register_skill("teamops", skill_path, description="When to use each TeamOps tool")
    except Exception:  # noqa: BLE001 — skill registration must not break tools
        pass
    try:
        from . import cli
        ctx.register_cli_command(
            name="teams",
            help="TeamOps: build, run, watch, fix and grow your agent team",
            description="Deploy blueprints, verify with Deploy Guard, rollback, generate via "
                        "the Agent Factory, fleet status, Cron911, Model Guard, standup, mesh sync.",
            setup_fn=cli.register_cli,
            handler_fn=cli.teams_command,
        )
    except Exception:  # noqa: BLE001 — CLI registration must not break tool registration
        pass
