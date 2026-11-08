"""TeamOps: build, run, watch, fix and grow your Hermes agent team."""
from __future__ import annotations

import json

from . import schemas, tools


def register(ctx) -> None:
    """Register every tool declared in schemas.SCHEMAS."""
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
