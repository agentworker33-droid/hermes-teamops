# hermes-teamops

Build, run, watch, fix, and grow a multi-agent team — one plugin.

**TeamOps** = blueprint installer (deploy a whole team: profiles, SOULs, skills, crons, kanban, mesh)
+ Agent Factory (Spec-Architect → Script-Builder → Validator generates new/custom blueprints)
+ Deploy Guard (preflight/smoke/rollback, ported from validate-onboard.sh)
+ fleet status + mesh view + Cron911 (silent-gap detection) + Model Guard + team standup + mesh sync.

CLI: `hermes teams <command>` — list, deploy, verify, rollback, factory, status, cron-scan, model-scan, standup, sync.

Plan: `~/.hermes/plans/2026-11-08_0450-hermes-teamops-plugin.md`
Build worklog: `WORKLOG.md` (cron shifts append here after every task).

Status: **D0 — scaffolding. Building for the Hermes Plugin Hackathon (deadline Nov 11 5PM EST).**
