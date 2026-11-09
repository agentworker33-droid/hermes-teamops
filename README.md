# hermes-teamops

Build, run, watch, fix, and grow a multi-agent team — one plugin.

**TeamOps** = blueprint installer (deploy a whole team: profiles, SOULs, skills, crons, kanban, mesh)
+ Agent Factory (generate custom blueprints from a spec)
+ Deploy Guard (preflight / smoke / rollback)
+ fleet status + mesh view + Cron911 (silent-gap detection) + Model Guard + team standup + mesh sync.

CLI: `hermes teams <command>`. Tool surface mirrors it: `blueprint_list`, `team_deploy`,
`team_verify`, `team_rollback`, `team_factory`, `team_status`, `cron_scan`, `model_scan`,
`model_rollback`, `team_standup`, `mesh_sync`, `team_dashboard`, `team_dashboard_status`,
`team_dashboard_stop`.

Plan: `~/.hermes/plans/2026-11-08_0450-hermes-teamops-plugin.md`
Build worklog: `WORKLOG.md` (cron shifts append here after every task).

Status: **D2 — Watch/Fix/Grow implemented + tested + Task 2.8 web dashboard
(`status --html`, interactive mesh graph) + live dashboard server
(`teams dashboard`, daemon + /health + stop) + Task 2.9 OPT-IN auto model-swap
(`model-scan --swap --pool a,b [--apply]`, changelog + `model-rollback`).
90 tests. Next: stretch 2.10–2.12 (vertical ports, VPS, Twilio) then D3 ship
(PR, video, form).**

## Install

```bash
# from a local checkout
hermes plugins install ./hermes-teamops
# or from the catalog once published (D3)
hermes plugins install hermes-teamops
```

Verify with the same checks the catalog CI runs:

```bash
hermes plugins validate ~/hermes-teamops --install-deps
hermes plugins doctor ~/hermes-teamops
```

## Quickstart (against a TEMP home — never your live box unless you mean it)

```bash
export HERMES_HOME=$(mktemp -d)   # scratch home for the demo
hermes teams list                 # sales, property-mgmt
hermes teams deploy sales         # profiles + SOULs + crons + kanban + mesh
hermes teams verify sales         # Deploy Guard table, all PASS
hermes teams status               # fleet panel: profiles, memory %, crons, mesh
hermes teams status --html        # self-contained dashboard (opens in browser; --no-open to write only)
hermes teams dashboard            # LIVE dashboard server (fresh data per request; daemon + /health)
hermes teams dashboard-status     # is the live server running?
hermes teams dashboard-stop       # stop it
hermes teams cron-scan            # Cron911: silent gaps + failures + dead models
hermes teams model-scan           # Model Guard: dead models + fallback suggestions
hermes teams model-scan --swap --pool deepseek/deepseek-v4-flash-0731,nous/hermes-3.5-flash
                                  # OPT-IN auto-swap: dry-run plan only (add --apply to write;
                                  # every swap is changelogged + revertible)
hermes teams model-rollback       # undo the most recent swap (--all for everything)
hermes teams standup              # last-24h digest + open kanban
hermes teams sync "use paper trading only" --author dalton   # MESH-MAP.md broadcast
hermes teams rollback sales       # undo the deploy
```

Target-home override: every CLI command accepts `--home <dir>` (`cron-scan`,
`model-scan`, `model-rollback`, `standup`, `sync`, `deploy`, `verify`, `rollback`,
`factory`, `dashboard`, `dashboard-status`, `dashboard-stop`).

## Demo (90 seconds)

`demo/demo-90s.sh` runs the scripted flow: deploy sales → verify → status →
inject a zombie cron fixture → cron-scan flags it → factory add → standup.
Dress-rehearsed on a temp HERMES_HOME in D2.6.

## Blueprints (v0.1)

A blueprint is a directory: `team.yaml` (roles/crons), `souls/<role>.md`,
`crons.yaml`, `assets/kanban.json`, `assets/mesh.json`, `verify.yaml`.
Baked v1: `sales` (6 roles), `property-mgmt` (owner / tenant-line boundary /
maintenance coordinator / books), plus the `fixtures/billing-agent` fallback
used by the Agent Factory when the live lane is unavailable.

## Modules

| File | Responsibility |
|---|---|
| `build.py` | blueprint loader + deploy/verify/rollback orchestration |
| `guard.py` | Deploy Guard: preflight / deploy / smoke / rollback (local, not VPS) |
| `factory.py` | Agent Factory wrapper (fixture fallback) |
| `fleet.py` | fleet reads: profiles, jobs.json, executions.db, memory %, mesh; team grouping + ephemeral-team inventory |
| `dashboard.py` | self-contained HTML dashboard (fleet panel, cron health, interactive SVG mesh graph; no CDN) |
| `server.py` | LIVE dashboard server: fresh HTML per request, /health, daemonize, stop (mnemosyne-dashboard pattern) |
| `cron911.py` | silent-gap + failure + dead-model detection (detect-only; multi-rule rows merged) |
| `modelguard.py` | dead-model detect + recommend; OPT-IN swap to a user-configured fallback pool (config `modelguard.fallback_pool`), changelog + rollback |
| `standup.py` | last-24h digest generator |
| `meshbridge.py` | MESH-MAP.md append + shared-Mnemosyne outbox |
| `tools.py` / `schemas.py` / `cli.py` | tool handlers, schemas, `hermes teams` CLI |

## Tests

```bash
python3 -m pytest -q
```

90 tests: blueprint loader, Deploy Guard (mocked hermes), factory fallback,
fleet/cron911/modelguard/standup/meshbridge/dashboard/server against a fake HERMES_HOME
built with the real jobs.json + executions.db schema, plus a manifest-coherence
test pinning plugin.yaml ⇄ schemas ⇄ handlers and model-swap pool/rollback tests.

## Cut list (NOT in v1)

Desktop/Electron panel (CLI-first; `status --html` gives you a self-contained
HTML dashboard), Twilio/Tenant Line, VPS provisioning (that's the website
product), >2 baked blueprints (factory fixtures cover more), marketplace/payments.
Auto model-swap IS in v1 but strictly OPT-IN: it only ever swaps to models the
user listed in `modelguard.fallback_pool` (`<home>/config/teamops.yaml` or
`--pool`) — no pool, no swap; every applied swap is changelogged and revertible
via `hermes teams model-rollback`.
