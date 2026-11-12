#!/usr/bin/env python3
"""Remote TeamOps driver — runs deploy/verify/rollback handlers on a target host.

Invoked by teamops.vps over ssh as::

    cd <staging> && PYTHONPATH=<staging> HERMES_HOME=<remote_home> \\
        TEAMOPS_NAME=<blueprint> python3 - <op>

The script is piped via stdin (``python3 -``), so it relies on cwd/PYTHONPATH
for imports and reads its inputs from environment variables. It prints the
exact same JSON the local tools return, letting the client merge the remote
Deploy Guard summary into its own report.
"""
from __future__ import annotations

import json
import os
import sys


def main() -> int:
    op = sys.argv[1] if len(sys.argv) > 1 else "deploy"
    name = os.environ.get("TEAMOPS_NAME", "")
    home = os.path.expanduser(os.environ.get("HERMES_HOME") or "~/.hermes") or None
    if not name:
        print(json.dumps({"success": False, "error": "TEAMOPS_NAME not set"}))
        return 1
    sys.path.insert(0, os.getcwd())  # staging dir: contains the teamops/ package
    try:
        from teamops import tools
    except ImportError as e:
        print(json.dumps({"success": False, "error": f"plugin bundle not importable: {e}"}))
        return 1
    if op == "deploy":
        out = tools.team_deploy({"name": name, "home": home, "verify": False})
    elif op == "verify":
        out = tools.team_verify({"name": name, "home": home})
    elif op == "rollback":
        out = tools.team_rollback({"name": name, "home": home})
    else:
        out = json.dumps({"success": False, "error": f"unknown op {op!r}"})
    print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
