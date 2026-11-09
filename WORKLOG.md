# TeamOps Build Worklog

> Every cron shift appends a row after EACH task. Resume from the last committed task.
> Order: D0 scaffold → D1 Build module → D2 Watch/Fix/Grow → D3 ship.

## Status
- Phase: **D2 — Watch/Fix/Grow COMPLETE (evening shift) + Task 2.7 LIVE factory lane PROVEN (night shift)**
- Next task: D3 ship (catalog entry + PR, demo video, submission form) — buffer till Nov 11 5PM EST
- Deadline: **Nov 11 2026 5PM EST**

## 2026-11-08 D2.7 — night shift (Task 2.7 LIVE factory lane test — SUCCESS)
- [x] ✅ **Task 2.7: LIVE factory lane test** — `agentic-coding-team.sh run --auto --lane agent-factory "3-agent lawn-care ops team"` ran to completion: Spec-Architect 21:33 → Script-Builder 21:38 → Validator 21:45 (**total ~884s / 14.7 min**). Validator handoff `onboard-script.json`: full lane gate **15/15 PASS** (bash -n ×2, roster audit, pricing $350, cron audit ×3, phantom-agent, placeholder/DEV-MODE negatives N1–N7). Generated artifacts: onboard-lawn-care.sh, verify-lawn-care.sh, MESH-MAP.md (3 edges), INBOX-dispatcher/field-agent/boss, README.md.
- [x] **Two real bugs found + fixed in the LIVE materialization path** (the D1 "fixture fallback" masked both):
  1. `_extract_workspace` token-split on spaces → failed on task ids like "3-agent lawn-care ops team-…". Fixed: rest-of-line after `task workspace:`/`Done. Workspace:` marker + ANSI strip.
  2. `blueprint_from_handoff` couldn't parse the real agent-factory schema (onboard-script.json has `files_created` absolute paths, no embedded team.yaml). Added `_blueprint_from_artifacts` converter → builds team.yaml (roles from roster), crons.yaml (from DRY_RUN_CRON dry-run lines), souls (from INBOX contract), assets/mesh.json (drop-file edges), assets/kanban.json, raw artifacts under assets/generated/. Plus `_find_handoff` disk fallback (searches ~/.hermes/ng-team/workspace new-newest, space-safe).
- [x] **Materialized live-generated blueprint**: `teamops/blueprints/lawn-care-ops/` (dispatcher → field-agent → boss, crons lawncare-co-dispatcher-daily-dispatch `0 8 * * *` / field-agent-weekly-inspection `0 9 * * 6` / boss-morning-brief `0 7 * * *`). `python3 -m teamops.cli teams list` shows it (count: 4).
- [x] **E2E on TEMP HERMES_HOME (no live touches)**: deploy lawn-care-ops 9/0 GREEN → verify 14/0 GREEN → rollback 9/0 GREEN → temp home removed. **Live home untouched** (0 teamops-* profiles).
- [x] **Model state**: ng-architect/ng-developer/ng-tech-lead pinned `deepseek/deepseek-v4-flash-0731` (nous) — lane ran with **0 dead-model flags** (model-scan confirms no ng-* flags; live home still reports the known pre-existing 51 trading LongCat 404s, out of repo scope, detect-only).
- [x] Tests: +4 regression tests (space-safe extraction, disk handoff fallback, artifacts→blueprint converter incl. artifact = destination INBOX), **47 GREEN**.
- [x] Commit: 4200003


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

## 2026-11-08 D2 — evening shift (largest)
- [x] Task 2.1: fleet.py — profile_dirs (hidden-dir filtered), jobs/executions/memory/mesh reads, fleet_report + mermaid graph; wired team_status tool + CLI
- [x] Task 2.2: cron911.py cron_scan (silent gap via cadence gaps, failure_streak, last_status, delivery errors, never-ran, DEAD MODEL markers, DISABLED info) + modelguard.py model_scan (pinned + profile-default dead-model detect, recommend-only)
- [x] Task 2.3: standup.py team_standup (last-24h outcomes per profile + open kanban counts)
- [x] Task 2.4: meshbridge.py mesh_sync (append MESH-MAP.md author-attributed + shared-Mnemosyne outbox fallback)
- [x] Fixes over previous-shift D2 code: hidden-dir filter, modelguard profile-default dead models, cron_scan home dir-vs-jobs.json resolution + db_override, graph in fleet report
- [x] Tests: tests/conftest FakeHome builder (real jobs.json/executions.db schema) + test_fleet/test_cron911/test_modelguard/test_standup/test_meshbridge — 42 total GREEN
- [x] Verified all four Watch/Fix/Grow tools against LIVE read-only data (104 jobs scanned, 51 dead-model flags incl. LongCat 404s, standup digest computed)
- [ ] Task 2.6: full 90s demo dress rehearsal on TEMP HERMES_HOME

## 2026-11-08 D2 — evening shift (Task 2.5 + critical guard fix)
- [x] Task 2.5: bundled skill teamops/skills/teamops/SKILL.md (when-to-use table + rules); README rewritten (install/quickstart/demo/tests/modules/cut list); demo script teamops/demo/demo-90s.sh + zombie-cron injector
- [x] **CRITICAL FIX — home-isolation leak**: `hermes profile create` resolves profile root to the NATIVE home when HERMES_HOME sits inside ~/.hermes (our TMPDIR scratch) — first demo runs LEAKED 14 empty test profiles into the LIVE box. Cleaned via `hermes profile delete` (verified clean). Added preflight `home-isolation` check (FAIL when target home is a strict subdir of the default root) + regression test. Demo temp home now uses /tmp (outside ~/.hermes) with comment documenting why. Verified: profile create lands in temp home, NO leaks.
- [x] CLI/--home plumbing: status/standup/sync now accept --home (README claimed it, parsers lacked it); tool schemas include home/team overrides
- [x] Rollback output sanitized (single-line details, no raw hermes prompts in table)
- [x] Tests: 43 GREEN (added home-isolation guard test)

## 2026-11-08 D2 — evening shift (Task 2.6 dress rehearsal)
- [x] Task 2.6: full 90s demo dress rehearsal run TWICE on temp HERMES_HOME (outside live home) — both GREEN end-to-end: doctor OK → list 2 blueprints → deploy sales (11/0) → verify (18/0/4) → status (6 profiles) → zombie cron injected → cron-scan flags SILENT GAP → model-scan clean → factory billing-agent --deploy (22/0) → standup → sync (2 mesh maps, author dalton) → rollback (11/0) → temp home removed. **NO leaks into live home** (verified after both runs).
- [x] Verification gates pass: `hermes plugins validate teamops --install-deps` → "Validation passed" (all 16 checks incl. capability probe + security scan); `hermes plugins doctor <temp>/plugins/teamops` OK; `pytest` 43 GREEN


## STRETCH — uncut vision (boss-live 2026-11-08 ~23:10, Dalton-approved; do in order, keep demo-green after each)
- [ ] Task 2.8: Web dashboard + interactive mesh graph — `hermes teams status --html` writes a self-contained HTML page (fleet panel: per-profile memory bars, cron health, execution stats; mesh map rendered clickable via a JS graph lib inline, no external CDN at runtime needed); opens in browser; CLI and tool return the file path. Tests: golden HTML from FakeHome. Commit. Re-run full suite + validate + dress rehearsal after.
- [ ] Task 2.9: Auto model-swap (OPT-IN) — Model Guard gains `swap` mode: on dead-model detection swap `model.default` to first live fallback in a user-configured pool (config key modelguard.fallback_pool); never invents models, never runs without the pool set; changelog + rollback command. Tests for pool logic + rollback. 
- [ ] Task 2.10: 3 more baked blueprints via the LIVE factory lane (insurance-claims, real-estate, trading-desk): run agentic-coding-team.sh --lane agent-factory per vertical, Validator gate each, hand-verify, commit as baked blueprints with verify.yaml checks. Fix factory converter bugs surfaced.
- [ ] Task 2.11: VPS provisioning module (OPTIONAL target) — `team_deploy --host user@host` optional SSH transport (scp blueprint + run remote install via ssh, preflight remote RAM/disk/python, remote smoke, rollback). Requires user-supplied SSH creds; clearly documented; keep local default. Tests: mock SSH runner.
- [ ] Task 2.12: Twilio phone intake module — inbound call/SMS handler wiring (twilio webhook → transcribe → intent triage → structured card → agent follow-up), mock-twilio mode for tests/demo; REAL calls blocked until Dalton provides Twilio creds via vault (do not ask in chat). Tests: mock-only.
- [ ] Freeze additions: **Nov 10 7PM EST** — after that only fixes; Nov 11 = PR final pin + video + form only.
