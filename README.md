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
(`model-scan --swap --pool a,b [--apply]`, changelog + `model-rollback`) + Task
2.10 ported vertical blueprints (`insurance-claims`, `real-estate`, `trading`
— the shipped onboard-*.sh teams) + rollback empty-dir fix + Task 2.11 OPTIONAL
VPS provisioning (`deploy/verify/rollback --host user@host[:port]` — remote
transport over ssh using YOUR OWN credentials; graceful no-creds degrade).
126 tests. Next: stretch 2.12 (Twilio), then D3 ship (PR, video, form).**

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
hermes teams deploy sales --host root@vps.example.com        # OPTIONAL: deploy REMOTELY over ssh
hermes teams verify sales --host root@vps.example.com        # remote Deploy Guard smoke
hermes teams rollback sales --host root@vps.example.com      # remote rollback
                                  # (uses YOUR OWN ssh credentials — agent / ~/.ssh keys,
                                  #  or --ssh-key ~/.ssh/id_ed25519; --remote-home to target
                                  #  a custom HERMES_HOME on the host. No creds stored/shipped.)
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
maintenance coordinator / books), `lawn-care-ops` (factory-materialized), plus
three **ported verticals** from the shipped onboarding runbooks: `insurance-claims`
(13 roles — claims-manager, intake, triage, coverage, adjuster, fraud, reserves,
compliance, monitor + 4 satellites), `real-estate` (14 roles — broker + listing/
buyer agents, inside sales, transaction coordinator, marketing, ops, client care,
document processor, market intelligence + industry tier), `trading` (10 roles —
boss, researcher, analyst, executor, risk-manager, portfolio-manager, monitor +
polymarket/launch-sniper/yield-farmer satellites). The `fixtures/billing-agent`
fallback used by the Agent Factory when the live lane is unavailable.

## Modules

| File | Responsibility |
|---|---|
| `build.py` | blueprint loader + deploy/verify/rollback orchestration |
| `guard.py` | Deploy Guard: preflight / deploy / smoke / rollback (local, not VPS) |
| `vps.py` | OPTIONAL VPS provisioning: deploy/verify/rollback `--host user@host[:port]` — remote preflight (python/disk/RAM/home), tar-stream bundle upload, remote driver `scripts/remote-driver.py`, staging cleanup; creds = the RUNNING USER's own ssh (agent/keys or `--ssh-key`), never stored; no ssh/auth → clean disable, locals untouched |
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

126 tests: blueprint loader (+ structural integrity for all 6 baked blueprints:
roles→souls, cron→role references, unique prefixed cron names, mesh→role edges,
kanban assignees), Deploy Guard (mocked hermes), factory fallback,
fleet/cron911/modelguard/standup/meshbridge/dashboard/server against a fake HERMES_HOME
built with the real jobs.json + executions.db schema, VPS provisioning via a
FAKE ssh runner (host parsing, remote preflight decisions, bundle upload,
remote deploy/verify/rollback, graceful no-creds degrade), plus a
manifest-coherence test pinning plugin.yaml ⇄ schemas ⇄ handlers and
model-swap pool/rollback tests.

## Cut list (NOT in v1)

Desktop/Electron panel (CLI-first; `status --html` gives you a self-contained
HTML dashboard), Twilio/Tenant Line, marketplace/payments.
VPS provisioning IS in v1 as an OPTIONAL ssh transport (`--host`): it always
uses the running user's own ssh credentials (agent / ~/.ssh / `--ssh-key` —
never stored or shipped by the plugin) and degrades to a clean disable message
when ssh/auth is absent; managed VPS delivery remains the paid website product.
Auto model-swap IS in v1 but strictly OPT-IN: it only ever swaps to models the
user listed in `modelguard.fallback_pool` (`<home>/config/teamops.yaml` or
`--pool`) — no pool, no swap; every applied swap is changelogged and revertible
via `hermes teams model-rollback`.
