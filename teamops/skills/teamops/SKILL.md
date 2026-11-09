---
name: teamops
description: When and how to use the hermes-teamops plugin tools — deploy a team blueprint, verify with Deploy Guard, roll back, generate via the Agent Factory, or run fleet status / Cron911 / Model Guard / standup / mesh sync.
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
| `blueprint_list` / `hermes teams list` | "What teams can I deploy?" — sales, property-mgmt |
| `team_deploy` / `hermes teams deploy <name>` | "Stand up the sales team" — creates profiles, SOULs, crons, kanban, mesh under a TEMP or real HERMES_HOME |
| `team_verify` / `hermes teams verify <name>` | After any deploy/factory run — run Deploy Guard preflight/smoke and show the PASS/FAIL table |
| `team_rollback` / `hermes teams rollback <name>` | Remove a deployed team (profiles, crons, shared files) from a snapshot |
| `team_factory` / `hermes teams factory "<spec>"` | Generate a custom blueprint from a natural-language spec (fixture fallback by default) |
| `team_status` / `hermes teams status` | "Show me the fleet" — per-profile memory %, crons, executions, mesh edges |
| `cron_scan` / `hermes teams cron-scan` | "Is anything silently failing?" — silent gaps, failure streaks, dead models |
| `model_scan` / `hermes teams model-scan` | "Is my model dead?" — 404/free-gone pinned or profile-default models + fallback suggestions |
| `team_standup` / `hermes teams standup` | "What did the team do in the last 24h?" — cron outcomes + open kanban tasks |
| `mesh_sync` / `hermes teams sync "<decision>"` | Broadcast a decision to every team's MESH-MAP.md (author-attributed) |

## Rules to follow

1. **Deploy Guard before green:** never report a team as "deployed" on the
   deploy output alone — run `team_verify` and only claim success when the
   PASS/FAIL table is green.
2. **Temp home for tests:** when the user just wants to try it, run against a
   scratch HERMES_HOME under /tmp, never the live box, unless they explicitly
   ask for the live deploy.
3. **Detect, don't auto-fix:** `cron_scan` and `model_scan` only detect and
   recommend. If the user asks to fix, suggest `hermes cron edit <id> --model <m>`
   or `hermes cron pause <id>` — do NOT silently rewrite live jobs.
4. **Secrets:** never ask for API keys in chat. Point to `hermes-vault add`
   or the Autonomous Secrets Store.
5. **Honesty:** if a scan returns no issues, say healthy. If a tool errored,
   report the error verbatim rather than fabricating a clean result.

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

- `team_factory` may fall back to the pre-baked `billing-agent` fixture when
  the live Agent Factory lane is unavailable — that is by design (plan §1.5);
  the result still deploys and verifies.
- `mesh_sync` writes MESH-MAP.md locally; if the shared Mnemosyne surface
  isn't importable it leaves an outbox entry in `~/.hermes/teams/.mesh-outbox.jsonl`.
