# hermes-teamops

Build, run, watch, fix, and grow a multi-agent team — one plugin.

**TeamOps** = blueprint installer (deploy a whole team: profiles, SOULs, crons, kanban, mesh)
+ Agent Factory (in-plugin template generator: extend a base blueprint with a role, or scaffold a new team)
+ Deploy Guard (preflight / smoke / rollback)
+ fleet status + mesh view + Cron911 (silent-gap detection) + Model Guard + team standup + mesh sync.

CLI: `hermes teams <command>`. Tool surface mirrors it: `blueprint_list`, `team_deploy`,
`team_verify`, `team_rollback`, `team_factory`, `team_status`, `cron_scan`, `model_scan`,
`model_rollback`, `team_standup`, `mesh_sync`, `team_dashboard`, `team_dashboard_status`,
`team_dashboard_stop`, `phone_intake`, `twilio_status`.

Plan: `~/.hermes/plans/2026-11-08_0450-hermes-teamops-plugin.md`
Build worklog: `WORKLOG.md` (cron shifts append here after every task).

Status: **catalog review round 3 fixes landed (Nov 12 morning)** — PR #151675
re-review points (reviewing `5e60e79`): the model can no longer pass CLI-only
path keys in its args dict at all — schema keys are read from `args` only and
`home`/`path`/`out_path`/`prefix`/`jobs_path`/`db_path`/`verify` are accepted
exclusively through `**kwargs` (the CLI / tests / remote driver), so
`team_status({"html": true, "out_path": …})`, `blueprint_list({"path": …})`,
`cron_scan({"db_path": …})` and `mesh_sync({"home": …})` are inert from the
model; Twilio creds now read ONLY through `agent.secret_scope.get_secret`
(the profile scope's own env fallback — no bare `os.environ` override, so one
launch profile's creds can never leak into another profile's plugin call);
all 10 trading SOULs carry a PAPER-ONLY MANDATE banner and the executor /
yield-farmer / boss / satellites / portfolio-manager lines that said place
trades / return fills / execute rebalances now say simulate in the paper
ledger, never place real orders or move funds. Dashboard token file is
created 0o600 from birth. **209 tests.**
Round-2 fixes (Nov 11 evening, all 9 points): vps remote commands fully
shlex.quote()d + blueprint
names validated `^[a-z0-9-]{1,64}$` + hosts starting with `-` refused + `--`
before the ssh host; `team_verify --host`, `model_scan` swap-apply and
`model_rollback` promoted to owner actions (approval + cron refusal);
dashboard server hardened (per-start 0600 token for `/api/action`, no CORS,
Host must be loopback, `last_run` confined to `<home>/teams/<team>/`, 
`run_script` gets a minimal PATH/HOME/LANG env, deploy action disabled —
CLI approval only); model args filtered to schema keys + blueprint-path
containment on the bare-name branch; factory default root moved to
`<home>/plugin-data/teamops/blueprints/` and bundled blueprint names are
refused; Twilio creds read via `agent.secret_scope` (declared `optional_env`,
no `twilio.env`); trading blueprint is analysis/paper-only; remote driver
calls handlers with dict args; docs match all of it.
Round-1 fixes (Nov 10 evening, all 7 points): Hermes dict-args tool
contract + OpenAI-shape schemas; PyYAML declared in `python_dependencies`;
Agent Factory is template-only (no home-dir script runner); deploy/rollback/
factory-deploy are OWNER ACTIONS gated by a `pre_tool_call` approve hook and
refused in cron/unattended runs; blueprint/soul/shared paths confined to the
blueprint root; path args removed from tool schemas (CLI-only) +
`hermes_constants` home resolution; docs/skill match the code, skill
registered via `ctx.register_skill`.

## Install

```bash
# from a public repo, pinned to a commit (catalog entry uses the same repo + subdir: teamops)
hermes plugins install https://github.com/agentworker33-droid/hermes-teamops --ref <commit-sha> --force
# or from the catalog once published (D3)
hermes plugins install hermes-teamops
```

Notes: the catalog entry installs subdir `teamops/` (that's where `plugin.yaml` lives), so
point local runs at the subdir. The install scanner may ask for `--force` on a community
plugin the first time (it flags test-file patterns like fake VPS host strings — review the
findings, then override when you trust the source).

Verify with the same checks the catalog CI runs (repo root FAILS validate with
"no plugin.yaml" — the manifest is in `teamops/`):

```bash
hermes plugins validate ~/hermes-teamops/teamops --install-deps
hermes plugins doctor ~/hermes-teamops/teamops
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
hermes teams dashboard            # LIVE dashboard server on port 8781 (fresh data per request; daemon + /health)
hermes teams dashboard-status     # is the live server running?
hermes teams dashboard-stop       # stop it
hermes teams cron-scan            # Cron911: silent gaps + failures + dead models
hermes teams model-scan           # Model Guard: dead models + fallback suggestions
hermes teams model-scan --swap --pool deepseek/deepseek-v4-flash-0731,nous/hermes-3.5-flash
                                  # OPT-IN auto-swap: dry-run plan only (add --apply to write;
                                  # every swap is changelogged + revertible)
hermes teams model-rollback       # undo the most recent swap (--all for everything)
                                  # NOTE: --apply/rollback rewrites every profile's config.yaml /
                                  # jobs.json (not just one) — every write is changelogged + revertible
hermes teams standup              # last-24h digest + open kanban
hermes teams sync "use paper trading only" --author dalton   # MESH-MAP.md broadcast
hermes teams phone-intake --mock --channel sms --body "The hvac is broken"   # Twilio intake (OPTIONAL, mock-first)
hermes teams phone-intake --mock --channel call               # mock voice → auto transcript → card
hermes teams phone-intake --channel sms --body "help"         # LIVE — clean refusal until YOU set creds
hermes teams twilio-status                                   # enabled? masked output only
hermes teams rollback sales       # undo the deploy

# Twilio intake (OPTIONAL, mock-first): real inbound calls/SMS are BLOCKED
# until credentials are available from YOUR OWN environment — the plugin never
# stores or ships them (declared optional_env in plugin.yaml):
#   export TWILIO_ACCOUNT_SID=... TWILIO_AUTH_TOKEN=... TWILIO_PHONE_NUMBER=...
#   # or provide them via Hermes secret storage (agent.secret_scope.get_secret)
# Creds are read ONLY through the secret scope; when no profile is bound the
# scope falls back to the running user's env itself — a multi-profile Hermes
# process can never leak one launch profile's Twilio creds into another.
# The pipeline: webhook payload → (transcribe) → intent triage → structured
# card → agent follow-up ledger at <home>/plugin-data/teamops/intake/.
# Without creds: phone-intake returns a clean 'disabled' refusal, mock=True
# always works, and nothing else in the plugin is affected.
```

Target-home override: every CLI command accepts `--home <dir>` (`cron-scan`,
`model-scan`, `model-rollback`, `standup`, `sync`, `deploy`, `verify`, `rollback`,
`factory`, `dashboard`, `dashboard-status`, `dashboard-stop`).

## Demo (90 seconds)

`demo/demo-90s.sh` runs the scripted flow: deploy sales → verify → status →
inject a zombie cron fixture → cron-scan flags it → factory add → standup.
Dress-rehearsed on a temp HERMES_HOME in D2.6.

## Blueprints (v0.1)

A blueprint is a directory: `team.yaml` (roles), `souls/<role>.md`,
`crons.yaml`, `assets/kanban.json`, `assets/mesh.json` (Deploy Guard's smoke
checks are code-defined — no separate `verify.yaml` file).
Baked v1: `sales` (6 roles), `property-mgmt` (owner / tenant-line boundary /
maintenance coordinator / books), `lawn-care-ops` (factory-materialized), plus
three **ported verticals** from the shipped onboarding runbooks: `insurance-claims`
(13 roles — claims-manager, intake, triage, coverage, adjuster, fraud, reserves,
compliance, monitor + 4 satellites), `real-estate` (14 roles — broker + listing/
buyer agents, inside sales, transaction coordinator, marketing, ops, client care,
document processor, market intelligence + industry tier), `trading` (10 roles —
boss, researcher, analyst, executor, risk-manager, portfolio-manager, monitor +
polymarket/launch-sniper/yield-farmer satellites; **analysis/paper-only**: every
cron observes/scores/simulates, no real money, orders or wallet keys). `fixtures/billing-agent` is
the pre-baked demo output of the Agent Factory (`use_fixture=true`).

## Modules

| File | Responsibility |
|---|---|
| `build.py` | blueprint loader + deploy/verify/rollback orchestration |
| `guard.py` | Deploy Guard: preflight / deploy / smoke / rollback (local, not VPS) |
| `vps.py` | OPTIONAL VPS provisioning: deploy/verify/rollback `--host user@host[:port]` — remote preflight (python/disk/RAM/home), tar-stream bundle upload, remote driver `scripts/remote-driver.py`, staging cleanup; creds = the RUNNING USER's own ssh (agent/keys or `--ssh-key`), never stored; blueprint names validated `^[a-z0-9-]{1,64}$`, every remote shell value shlex-quoted, `--` before the host, hosts starting with `-` refused; no ssh/auth → clean disable, locals untouched |
| `factory.py` | Agent Factory (template-only generator: extend a base blueprint with a role, or scaffold a team; fixture fallback for demos). Writes under `<home>/plugin-data/teamops/blueprints/` (or config `factory.blueprints_root`); bundled blueprint names are refused |
| `fleet.py` | fleet reads: profiles, jobs.json, executions.db, memory %, mesh; team grouping + ephemeral-team inventory |
| `dashboard.py` | self-contained HTML dashboard (fleet panel, cron health, interactive SVG mesh graph; no CDN) |
| `server.py` | LIVE dashboard server: fresh HTML per request, /health, daemonize, stop (mnemosyne-dashboard pattern). Loopback-only + Host check, NO CORS, per-start token (created 0o600 from birth via os.open) gates `/api/action`; `last_run` confined to `<home>/teams/<team>/`; `run_script` spawns with a minimal PATH/HOME/LANG env; deploy action disabled (CLI approval only) |
| `twilio_intake.py` | OPTIONAL Twilio phone intake: webhook payload → transcribe → intent triage → structured card → agent follow-up ledger; creds read ONLY through Hermes secret scope (`agent.secret_scope.get_secret`, declared `optional_env`, never stored/shipped) — the scope's own env fallback covers unbound profiles, no bare `os.environ` read; mock-first, no-creds → clean disable |
| `cron911.py` | silent-gap + failure + dead-model detection (detect-only; multi-rule rows merged) |
| `modelguard.py` | dead-model detect + recommend; OPT-IN swap to a user-configured fallback pool (config `modelguard.fallback_pool`), changelog + rollback |
| `standup.py` | last-24h digest generator |
| `meshbridge.py` | MESH-MAP.md append + shared-Mnemosyne outbox |
| `tools.py` / `schemas.py` / `cli.py` | tool handlers, schemas, `hermes teams` CLI |

## Tests

```bash
python3 -m pytest -q
```

209 tests: blueprint loader (+ structural integrity for all 6 baked blueprints:
roles→souls, cron→role references, unique prefixed cron names, mesh→role edges,
kanban assignees, trading paper-only flag), Deploy Guard (mocked hermes;
rollback scope confinement; cron.allow_agent_scheduling honouring), factory
template generator + fixtures + bundled-slug refusal + home plugin-data root,
fleet/cron911/modelguard/standup/meshbridge/dashboard/server against a fake HERMES_HOME
built with the real jobs.json + executions.db schema, VPS provisioning via a
FAKE ssh runner (host parsing incl. dash-host refusal, name validation, remote
preflight decisions, shlex-quoted remote commands, double-dash argv, bundle
upload, remote deploy/verify/rollback, graceful no-creds degrade), dashboard
server security (per-start token 403s, loopback Host check, no CORS, last_run
confinement, deploy disabled, minimal run_script env), Twilio intake
config (secret-scope + env source, masked output), payload parsing, intent triage,
full mock pipeline + ledger, live-blocked-without-creds refusal, tool + CLI
wiring, plus a manifest-coherence test pinning plugin.yaml ⇄ schemas ⇄ handlers,
the Hermes dict-args handler contract (handler({"name": "sales"})), model-swap
pool/rollback tests, and the round-2 approval-hook matrix
(team_verify --host / model_scan swap-apply / model_rollback), plus the
round-3 key-confinement tests (real handlers ignore CLI-only keys inside the
model's args dict; kwargs still land exactly where the CLI asks) and the
secret-scope-wins-over-process-env Twilio test.

## Cut list (NOT in v1)

Desktop/Electron panel (CLI-first; `status --html` gives you a self-contained
HTML dashboard), marketplace/payments, Tenant Line (Twilio webhook + gateway
+ agent-boundary phone line stays the paid vertical).
VPS provisioning IS in v1 as an OPTIONAL ssh transport (`--host`): it always
uses the running user's own ssh credentials (agent / ~/.ssh / `--ssh-key` —
never stored or shipped by the plugin) and degrades to a clean disable message
when ssh/auth is absent; managed VPS delivery remains the paid website product.
Twilio phone intake IS in v1 but mock-first and strictly OPTIONAL: real
inbound calls/SMS are blocked until the user's own `TWILIO_ACCOUNT_SID` /
`TWILIO_AUTH_TOKEN` / `TWILIO_PHONE_NUMBER` exist (Hermes secret scope or the
running user's env — declared `optional_env` in plugin.yaml); no creds →
clean 'disabled' refusal, `mock=True`
always works, outputs are masked, and the plugin never stores or ships
credentials.
Auto model-swap IS in v1 but strictly OPT-IN: it only ever swaps to models the
user listed in `modelguard.fallback_pool` (`<home>/config/teamops.yaml` or
`--pool`) — no pool, no swap; every applied swap is changelogged and revertible
via `hermes teams model-rollback`.
