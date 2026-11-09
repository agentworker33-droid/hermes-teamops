"""CLI group for `hermes teams ...`.

Follows the plugin CLI pattern (see mnemosyne): ``register_cli(subparser)``
declares subcommands, ``teams_command(args)`` dispatches to the same handlers
as the tools so CLI and tool surface always stay in sync.
"""
from __future__ import annotations

import json
import sys

from . import tools


def register_cli(subparser):
    """Register `hermes teams <command>` subcommands on the given subparser."""
    sub = subparser.add_subparsers(dest="teams_cmd")

    p = sub.add_parser("list", help="List blueprints")
    p.add_argument("--path", default=None, help="Optional blueprint root dir")
    p.set_defaults(func=_cmd_list)

    p = sub.add_parser("deploy", help="Deploy a blueprint locally")
    p.add_argument("name", help="Blueprint name, e.g. 'sales'")
    p.add_argument("--prefix", default=None, help="Profile name prefix")
    p.add_argument("--dry-run", action="store_true", help="Print plan without creating anything")
    p.add_argument("--home", default=None, help="Target HERMES_HOME (default: $HERMES_HOME or ~/.hermes)")
    p.add_argument("--no-verify", action="store_true", help="Skip Deploy Guard verify after deploy")
    p.set_defaults(func=_cmd_deploy)

    p = sub.add_parser("verify", help="Run Deploy Guard on a team")
    p.add_argument("name", help="Blueprint/team name to verify")
    p.add_argument("--home", default=None, help="Target HERMES_HOME")
    p.add_argument("--chat-probe", action="store_true", help="Also run a bounded agent chat probe (needs provider keys)")
    p.set_defaults(func=_cmd_verify)

    p = sub.add_parser("rollback", help="Roll back a team")
    p.add_argument("name", help="Blueprint/team name to roll back")
    p.add_argument("--home", default=None, help="Target HERMES_HOME")
    p.set_defaults(func=_cmd_rollback)

    p = sub.add_parser("factory", help="Generate a new blueprint via the Agent Factory")
    p.add_argument("spec", help="Natural-language spec of the team/roles to generate")
    p.add_argument("--base", default=None, help="Optional base blueprint to extend")
    p.add_argument("--name", default=None, help="Blueprint name override")
    p.add_argument("--use-fixture", action="store_true", help="Use the pre-baked fixture instead of a live lane run")
    p.add_argument("--deploy", action="store_true", help="Deploy + verify after generation")
    p.add_argument("--home", default=None, help="Target HERMES_HOME")
    p.set_defaults(func=_cmd_factory)

    p = sub.add_parser("status", help="Fleet status (JSON or self-contained HTML dashboard)")
    p.add_argument("team", nargs="?", default=None, help="Optional team name filter")
    p.add_argument("--home", default=None, help="Target HERMES_HOME")
    p.add_argument("--html", nargs="?", const="default", default=None, metavar="PATH",
                   help="Write a self-contained dashboard HTML file (optionally PATH); opens in browser unless --no-open")
    p.add_argument("--no-open", action="store_true",
                   help="With --html: write the file but do not open a browser")
    p.set_defaults(func=_cmd_status)

    p = sub.add_parser("cron-scan", help="Cron911 check: silent gaps + failures + dead models")
    p.add_argument("--home", default=None, help="Target HERMES_HOME")
    p.set_defaults(func=_cmd_cron_scan)

    p = sub.add_parser("model-scan", help="Model Guard check: dead models + recommendations")
    p.add_argument("--home", default=None, help="Target HERMES_HOME")
    p.set_defaults(func=_cmd_model_scan)

    p = sub.add_parser("standup", help="Team standup digest")
    p.add_argument("--team", default=None, help="Team name filter")
    p.add_argument("--hours", type=int, default=24, help="Lookback hours (default 24)")
    p.add_argument("--home", default=None, help="Target HERMES_HOME")
    p.set_defaults(func=_cmd_standup)

    p = sub.add_parser("sync", help="Broadcast a decision to all teams")
    p.add_argument("message", help="Decision/announcement to broadcast")
    p.add_argument("--author", default="teamops", help="Attribution label")
    p.add_argument("--team", default=None, help="Team filter")
    p.add_argument("--home", default=None, help="Target HERMES_HOME")
    p.set_defaults(func=_cmd_sync)

    p = sub.add_parser("dashboard", help="Start the LIVE dashboard server (mnemosyne-dashboard style)")
    p.add_argument("--port", type=int, default=8781, help="Port (default 8781)")
    p.add_argument("--no-open", action="store_true", help="Do not open a browser")
    p.add_argument("--home", default=None, help="Target HERMES_HOME")
    p.add_argument("--foreground", action="store_true", help="Keep the server in this terminal")
    p.set_defaults(func=_cmd_dashboard)

    p = sub.add_parser("dashboard-status", help="Is the live dashboard server running?")
    p.add_argument("--home", default=None)
    p.set_defaults(func=_cmd_dashboard_status)

    p = sub.add_parser("dashboard-stop", help="Stop the live dashboard server")
    p.add_argument("--home", default=None)
    p.set_defaults(func=_cmd_dashboard_stop)


def _print(data) -> int:
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except json.JSONDecodeError:
            print(data)
            return 0
    ok = data.get("success", True)
    if "checks" in data:
        from .guard import DeployGuard  # reuse the table renderer via a temp guard? no—render inline
        lines = [f"TeamOps — {data.get('team', '')}", "-" * 60]
        for c in data["checks"]:
            lines.append(f"  {c.get('status', '?'):<5} {c.get('name', ''):<28} {c.get('detail', '')}")
        s = data
        lines.append("-" * 60)
        lines.append(f"  PASS {s.get('pass', 0)} · FAIL {s.get('fail', 0)} · SKIP {s.get('skip', 0)} "
                     f"→ {'GREEN' if s.get('green') else 'NOT GREEN'}")
        print("\n".join(lines))
    elif "blueprints" in data and "count" in data:
        for b in data["blueprints"]:
            print(f"  {b['id']:<20} v{b.get('version', '?') or '?'} — {', '.join(b.get('roles') or [])}")
        print(f"count: {data['count']}")
    else:
        print(json.dumps(data, indent=2, ensure_ascii=False))
    return 0 if ok else 1


def _cmd_list(args) -> int:
    return _print(tools.blueprint_list(path=args.path))


def _cmd_deploy(args) -> int:
    r = json.loads(tools.team_deploy(args.name, prefix=args.prefix, dry_run=args.dry_run,
                                     home=args.home, verify=not args.no_verify))
    if "verify" in r:
        r["checks"] = (r.get("verify") or {}).get("checks", [])
        r["team"] = args.name
    return _print(r)


def _cmd_verify(args) -> int:
    r = json.loads(tools.team_verify(args.name, home=args.home))
    r["team"] = args.name
    return _print(r)


def _cmd_rollback(args) -> int:
    r = json.loads(tools.team_rollback(args.name, home=args.home))
    r["team"] = args.name
    return _print(r)


def _cmd_factory(args) -> int:
    return _print(tools.team_factory(args.spec, base=args.base, name=args.name,
                                     use_fixture=args.use_fixture, deploy=args.deploy,
                                     home=args.home))


def _cmd_status(args) -> int:
    if args.html is not None:
        out_path = None if args.html == "default" else args.html
        r = json.loads(tools.team_status(team=args.team, home=args.home,
                                         html=True, out_path=out_path))
        if r.get("success") and not args.no_open:
            import webbrowser
            webbrowser.open(r.get("html_path", ""))
        return _print(r)
    return _print(tools.team_status(team=args.team, home=args.home))


def _cmd_cron_scan(args) -> int:
    return _print(tools.cron_scan(jobs_path=args.home))


def _cmd_model_scan(args) -> int:
    return _print(tools.model_scan(home=args.home))


def _cmd_standup(args) -> int:
    return _print(tools.team_standup(team=args.team, hours=args.hours, home=args.home))


def _cmd_sync(args) -> int:
    return _print(tools.mesh_sync(message=args.message, author=args.author, team=args.team, home=args.home))


def _cmd_dashboard(args) -> int:
    if args.foreground:
        from . import server
        home = tools._resolve_home_arg(args.home)
        port = server.find_port(args.port)
        print(json.dumps({"success": True, "url": f"http://127.0.0.1:{port}/",
                          "foreground": True, "home": str(home)}, ensure_ascii=False))
        server.serve(home, port=port, open_browser=not args.no_open).serve_forever()
        return 0
    return _print(tools.team_dashboard(home=args.home, port=args.port,
                                       open_browser=not args.no_open))


def _cmd_dashboard_status(args) -> int:
    return _print(tools.team_dashboard_status(home=args.home))


def _cmd_dashboard_stop(args) -> int:
    return _print(tools.team_dashboard_stop(home=args.home))


def teams_command(args):
    """Handler registered via ctx.register_cli_command (set_defaults(func=...))."""
    fn = getattr(args, "func", None)
    if fn is None:
        print("Usage: hermes teams {list|deploy|verify|rollback|factory|status|cron-scan|model-scan|standup|sync}")
        return 1
    return fn(args) or 0


def main(argv=None) -> int:
    """Standalone entry: python -m teamops.cli teams list (dev/test convenience —
    mirrors the `hermes teams` plugin wiring)."""
    import argparse
    parser = argparse.ArgumentParser(prog="hermes", description="TeamOps CLI")
    sub = parser.add_subparsers(dest="command")
    teams_cmd = sub.add_parser("teams", help="TeamOps: build, run, watch, fix, grow your agent team")
    register_cli(teams_cmd)
    args = parser.parse_args(argv)
    return teams_command(args)


if __name__ == "__main__":
    sys.exit(main())
