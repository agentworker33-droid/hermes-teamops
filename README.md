# hermes-teamops

Build, run, watch, fix, and grow a multi-agent team — one plugin.

**TeamOps** = blueprint installer (deploy a whole team: profiles, SOULs, skills, crons, kanban, mesh)
+ Agent Factory (generate custom blueprints from a spec)
+ Deploy Guard (preflight / smoke / rollback)
+ fleet status + mesh view + Cron911 (silent-gap detection) + Model Guard + team standup + mesh sync.

CLI: `hermes teams <command>`. Tool surface mirrors it: `blueprint_list`, `team_deploy`,
`team_verify`, `team_rollback`, `team_factory`, `team_status`, `cron_scan`, `model_scan`,
`team_standup`, `mesh_sync`.

Plan: `~/.hermes/plans/2026-11-08_0450-hermes-teamops-plugin.md`
Build worklog: `WORKLOG.md` (cron shifts append here after every task).

Status: **D2 — Watch/Fix/Grow implemented + tested (54 tests) + Task 2.8 web dashboard (status --html, interactive mesh graph). D3 ship path remaining (catalog PR, demo video, form).**

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
hermes teams cron-scan            # Cron911: silent gaps + failures + dead models
hermes teams model-scan           # Model Guard: dead models + fallback suggestions
hermes teams standup              # last-24h digest + open kanban
hermes teams sync "use paper trading only" --author dalton   # MESH-MAP.md broadcast
hermes teams rollback sales       # undo the deploy
```

Target-home override: every CLI command accepts `--home <dir>` (`cron-scan`,
`model-scan`, `standup`, `sync`, `deploy`, `verify`, `rollback`, `factory`).

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
| `fleet.py` | fleet reads: profiles, jobs.json, executions.db, memory %, mesh |
| `dashboard.py` | self-contained HTML dashboard (fleet panel, cron health, interactive SVG mesh graph; no CDN) |
| `cron911.py` | silent-gap + failure + dead-model detection (detect-only) |
| `modelguard.py` | dead-model detect + recommend (never auto-swaps) |
| `standup.py` | last-24h digest generator |
| `meshbridge.py` | MESH-MAP.md append + shared-Mnemosyne outbox |
| `tools.py` / `schemas.py` / `cli.py` | tool handlers, schemas, `hermes teams` CLI |

## Tests

```bash
python3 -m pytest -q
```

54 tests: blueprint loader, Deploy Guard (mocked hermes), factory fallback,
fleet/cron911/modelguard/standup/meshbridge/dashboard against a fake HERMES_HOME
built with the real jobs.json + executions.db schema.

## Cut list (NOT in v1)

Desktop/Electron panel (CLI-first; `status --html` gives you a self-contained
HTML dashboard), Twilio/Tenant Line, VPS provisioning (that's the website
product), auto-swapping models (detect+recommend only),
>2 baked blueprints (factory fixtures cover more), marketplace/payments.
