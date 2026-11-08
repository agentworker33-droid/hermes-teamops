"""CLI group for `hermes teams ...` (wiring lands in D1; module reserved so
catalog CI sees the CLI entrypoint shape early)."""
from __future__ import annotations

import argparse


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="hermes teams", description="TeamOps: build, run, watch, fix, grow your agent team")
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("list", help="List blueprints")
    sub.add_parser("deploy", help="Deploy a blueprint locally")
    sub.add_parser("verify", help="Run Deploy Guard on a team")
    sub.add_parser("rollback", help="Roll back a team")
    sub.add_parser("factory", help="Generate a new blueprint via the Agent Factory")
    sub.add_parser("status", help="Fleet status")
    sub.add_parser("cron-scan", help="Cron911 check")
    sub.add_parser("model-scan", help="Model Guard check")
    sub.add_parser("standup", help="Team standup digest")
    sub.add_parser("sync", help="Broadcast a decision to all teams")
    return p
