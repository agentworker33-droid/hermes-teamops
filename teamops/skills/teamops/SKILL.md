---
name: teamops
description: When and how to use the hermes-teamops plugin tools — deploy a team blueprint, verify with Deploy Guard, roll back, generate via the Agent Factory template generator, or run fleet status / Cron911 / Model Guard / standup / mesh sync.
category: team-operations
---

# TeamOps — build, run, watch, fix, grow your agent team

**When to use:** the user asks to stand up an agent team, check whether their
agents/crons are healthy, get a digest of what agents did recently, or record a
decision for the whole team. The plugin ships one CLI (`hermes teams ...`) and
matching tools.

## Tool cheat-sheet

| Tool / CLI | Use it when... |
|---|---|
| `blueprint_list` / `hermes teams list` | "What teams can I deploy?" — sales, property-mgmt, lawn-care-ops, ported verticals |
| `team_deploy` / `hermes teams deploy <name>` | "Stand up the sales team" — creates profiles, SOULs, crons, kanban, mesh under a real HERMES_HOME. **Owner action:** runs only with human approval; never in cron/unattended runs. |
| `team_verify` / `hermes teams verify <name>` | After any deploy/factory run — run Deploy Guard preflight/smoke and show the PASS/FAIL table. Local verify is read-only; `--host` (remote) is an **owner action** (approval + cron refusal). |
| `team_rollback` / `hermes teams rollback <name>` | Remove a deployed team (profiles, crons, shared files) from a snapshot. **Owner action:** approval required; deletes only teamops-prefixed profiles and files under `<home>/teams/<team>/`. |
| `team_factory` / `hermes teams factory "<spec>"` | Generate a custom blueprint with the built-in template generator (extend a base blueprint with a role, or scaffold a new team). `--deploy` is an owner action. Generated blueprints land under `<home>/plugin-data/teamops/blueprints/`; bundled blueprint names are refused. |
| `team_status` / `hermes teams status` | "Show me the fleet" — per-profile memory %, crons, executions, mesh edges |
| `cron_scan` / `hermes teams cron-scan` | "Is anything silently failing?" — silent gaps, failure streaks, dead models |
| `model_scan` / `hermes teams model-scan` | "Is my model dead?" — 404/free-gone pinned or profile-default models + fallback suggestions. `--swap --apply` and `model-rollback` are **owner actions** (approval + cron refusal). |
| `team_standup` / `hermes teams standup` | "What did the team do in the last 24h?" — cron outcomes + open kanban tasks |
| `mesh_sync` / `hermes teams sync "<decision>"` | Broadcast a decision to every team's MESH-MAP.md (author-attributed) |

## Rules to follow

1. **Deploy Guard before green:** never report a team as "deployed" on the
   deploy output alone — run `team_verify` and only claim success when the
   PASS/FAIL table is green.
2. **Temp home for tests:** when the user just wants to try it, run against a
   scratch HERMES_HOME OUTSIDE the default hermes root — never the live box —
   unless they explicitly ask for the live deploy. Deploy Guard's
   `home-isolation` preflight refuses a target inside the default hermes root.
3. **Detect, don't auto-fix:** `cron_scan` and `model_scan` only detect and
   recommend. If the user asks to fix, suggest `hermes cron edit <id> --model <m>`
   or `hermes cron pause <id>` — do NOT silently rewrite live jobs. Model swap
   is opt-in and changelogged; never run it without an explicit pool.
4. **Secrets:** never ask for API keys in chat. Point the user to Hermes's own
   secret/credential flow for their keys — the plugin itself never stores
   credentials (Twilio/SSH read the RUNNING USER's env/setup only).
5. **Honesty:** if a scan returns no issues, say healthy. If a tool errored,
   report the error verbatim rather than fabricating a clean result.
6. **Blueprints are local data:** deploys read blueprints from the plugin's
   bundled `blueprints/` dir; the Agent Factory writes generated blueprints to
   `<home>/plugin-data/teamops/blueprints/` (or `factory.blueprints_root`).
   The plugin never writes outside those roots plus the target home's own
   `profiles/` and `teams/` directories, and bundled blueprint names are refused.

## Data conventions (dashboard reads these)

- A team under `$HERMES_HOME/teams/<name>/` (or a root-level team dir with a
  mesh marker) appears in the Teams view. Members are attached by profile-stem
  match (`trading-desk` covers `trading-*`) — for cross-stem teams add
  `team.json` with an explicit roster: `{"label": "...", "hub": "<role>",
  "members": ["a", "b", ...]}`. The hub is picked automatically if omitted.
- Mesh edges are parsed from `MESH-MAP.md` tables (`| E1 | A → B | artifact |`),
  ATX headings (`### A → B`), and mermaid lines. Prose that mentions arrows is
  ignored. White pulses on the diagram = typed handoffs; put a word like
  "handoff"/"Handoff (typed)" in the edge's artifact cell to draw one.
- Profiles are agents only if they carry SOUL.md / memories / cron / skills /
  config — workspace dirs under profiles/ (e.g. `trading-desk/state`) are
  ignored, so they never appear as phantom team members.

## Fallbacks

- `team_factory` uses the in-plugin template generator; `use_fixture=true`
  (demo) returns the pre-baked `billing-agent` fixture. The result still
  deploys and verifies.
- `mesh_sync` writes MESH-MAP.md locally; if the shared Mnemosyne surface
  isn't importable it leaves an outbox entry in `<home>/teams/.mesh-outbox.jsonl`.
