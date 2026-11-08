"""TeamOps: build, run, watch, fix and grow your Hermes agent team."""
from __future__ import annotations

import json

from . import schemas, tools


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
