# TeamOps Build Worklog

> Every cron shift appends a row after EACH task. Resume from the last committed task.
> Order: D0 scaffold → D1 Build module → D2 Watch/Fix/Grow → D3 ship.

## Status
- Phase: **D1 — Build module COMPLETE (evening shift)**
- Next task: D2 Watch/Fix/Grow (fleet status, Cron911, Model Guard, standup, mesh sync, bundled skill)
- Deadline: **Nov 11 2026 5PM EST**

## 2026-11-08 D0 (live shift)
- [x] Create repo skeleton (~/hermes-teamops, git init, .gitignore, README)
- [x] Plugin manifest + register() + schemas (10 tools declared)
- [x] tools.py with working `blueprint_list` + `team_status`; honest stubs for the rest
- [x] Module stubs: build (loader done), guard, fleet, cron911, modelguard, standup, meshbridge, factory, cli
- [x] blueprints/sales first draft (team.yaml + crons.yaml)
- [x] Verify: `hermes plugins doctor .` + `pytest tests/` + first commit
- [x] Set up 24/7 build crons (morning 8am c246054c4d36 / noon 12pm 99894c532b14 / evening 8pm b2b0332681b0 / sweep 2am 540639626cbc) — D1 kicked off immediately

## 2026-11-08 D1 — evening shift (largest)
- [x] Task 1.1: blueprints complete — property-mgmt (owner/tenant-line/maintenance/books, Tenant Line boundary rules), sales souls+assets, billing-agent fixture (commit 728b76e)
- [x] Task 1.3+1.4: Deploy Guard (preflight/deploy/smoke/rollback) + factory.py + CLI `hermes teams` + tool wiring + pytest 13 tests (commit d99dfea)
- [x] Refactor: plugin self-contained (blueprints under teamops/) so installed plugins resolve data (commit 66f5df8)
- [x] Task 1.5: E2E on temp HERMES_HOME all GREEN — deploy sales (11/0) → verify (18/0/4) → factory billing-agent deploy+verify (13/0, 22/0) → rollback (13/0) via real `hermes teams` CLI (commit 79919ca + fixes)
- [x] Task 1.2: factory.py live lane attempted — ng-architect profile pins dead model `meituan/longcat-2.5-preview:free` (404 no longer free) → fixture fallback exercised (by design, plan §1.5); fallback paths covered by tests/test_factory.py (18 total)
- [ ] D2: Watch/Fix/Grow — fleet status, Cron911, Model Guard, standup digest, mesh sync, bundled skill

## 2026-11-08 D1 — post-commit additions (rollback hardening)
- [x] Snapshot-driven rollback removes crons by job_id and tolerates missing blueprint (fixtures) — verified in E2E (commit 79919ca)
