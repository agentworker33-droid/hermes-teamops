# TeamOps Build Worklog

> Every cron shift appends a row after EACH task. Resume from the last committed task.
> Order: D0 scaffold → D1 Build module → D2 Watch/Fix/Grow → D3 ship.

## Status
- Phase: **D2 — Watch/Fix/Grow COMPLETE + 2.8 web dashboard + 2.8b live dashboard server + Task 2.9 auto model-swap (OPT-IN) DONE (noon shift). Next: Task 2.10 port vertical blueprints — freeze Nov 10 7PM EST**
- Next task: Task 2.10 PORT existing vertical teams into blueprint format (insurance-claims, real-estate, trading), then 2.11 VPS, 2.12 Twilio — then D3 ship
- Deadline: **Nov 11 2026 5PM EST**

## 2026-11-09 12:00 — NOON shift (Task 2.9 auto model-swap OPT-IN — DONE)
- [x] **Task 2.9: Model Guard `swap` mode (OPT-IN)** — `modelguard.swap(home, pool, dry_run=True)`: dead PINNED job models are swapped to the first *valid* pool fallback (jobs.json), dead UNPINNED jobs swap the scope's `model.default` in its config.yaml (home `config.yaml` or profile `config.yaml`; profiles with no config are skipped with a reason — never invented). Pool sources: plugin config `<home>/config/teamops.yaml` → `modelguard.fallback_pool`, or explicit `pool`/`--pool`. **Never invents models, never runs without a pool** — empty/missing pool → clean refusal `swap disabled — no fallback_pool configured` (acceptance rule: graceful degrade, zero nags).
- [x] **Changelog + rollback**: every applied swap recorded in `<home>/plugin-data/teamops/model-swaps.json` (scope, job_id, from→to, ts). `modelguard.rollback(home, all_swaps=False)` reverts the most recent un-reverted swap (LIFO) or `--all` in reverse order; drift (current value no longer == what we wrote) is skipped, never clobbered; records flagged `reverted`.
- [x] **Guardrails**: `valid_model()` regex (provider/model, no spaces); invalid pool entries filtered and reported (`invalid_pool`), never used; `changed` only written when a job actually matched (by id) and its model was the dead one; healthy jobs untouched; zero-swap applies no longer create an empty changelog file; JSON everywhere (changelog_path as str — fixed PosixPath serialization bug caught by tests).
- [x] **Surface**: `model_scan` tool schema gains `swap`/`pool`/`dry_run`/`home`; new `model_rollback` tool (+ schemas) — **14 tools** total; plugin.yaml declares it; CLI `model-scan --swap [--pool a,b] [--apply]` + `model-rollback [--all]`.
- [x] **Tests +20 → 90 GREEN** (`tests/test_modelguard_swap.py`): pool pick logic (current-skip/invalid/empty), valid_model regex, config read (missing/bad/valid), dry-run plan no-writes, apply pinned (jobs.json + changelog), apply scope default (config.yaml), profile-without-config skip, no-pool refusal w/ zero writes, config-pool fallback, invalid-entry filtering, rollback last + rollback --all (job+default), drift-skip, no-changelog benign, tool dry-run/apply, tool refusal, CLI apply-then-rollback (first pool entry wins).
- [x] Gates: `hermes plugins validate teamops --install-deps` → "Validation passed" (declared tools match); `hermes plugins doctor teamops/` → OK **14 tools**, 0 hooks.
- [x] **Live E2E on TEMP home `/tmp/teamops-swap`**: config pool picked up without --pool; dry-run plan 1 job + 1 default; `--apply` wrote both (jobs.json h1 → deepseek-v4-flash; config.yaml model.default → deepseek-v4-flash); `model-rollback` reverted the default (LIFO), changelog `reverted` flag set, drift 0; **no live leaks** (0 teamops-* profiles in live home). Temp home removed.
- [x] README: 14-tool surface, `--swap/--pool/--apply` + rollback quickstart, modelguard module row, 90 tests, cut-list section now documents the opt-in swap contract. Commit: **b39b408**.
- [x] Post-2.9 note: an out-of-band edit after the 2.9 commit corrupted `dashboard.py` (duplicated `+ (data.prompt || '')` in the inline toast JS → syntax error in shipped HTML) and improved `server.py` (bind exact port, no drift). Reverted the dashboard corruption to 655d6ae state, kept the server fix — commit **2e3ddb4**.


## 2026-11-09 12:00 — NOON shift (finish in-progress live dashboard server work left uncommitted)
- [x] **Finished uncommitted work found at shift start**: LIVE dashboard server (`teamops/server.py`, daemon + /health + stop + action dispatch), dashboard redesign (Mnemosyne-rail layout), cron911 multi-rule issue merging, fleet team enrichment + ephemeral-team inventory — code was green but never committed.
- [x] **Schemas gap fixed**: plugin.yaml declared team_dashboard / team_dashboard_status / team_dashboard_stop but schemas.py lacked them → register() never exposed them as LLM tools (validate warned). Added schemas; doctor now registers **13 tools** (10→13), validate 0 warnings.
- [x] **Real bug found + fixed**: fleet.parse_mesh_edges matched "->" before "-->" so "A --> B" parsed as ("-", B). Arrow matching is now longest-first ("-->", "->", "→").
- [x] **Tests +10 → 70 GREEN**: merge_issues collapse/dedup/sort + merged scan output; parse_mesh_edges (table + mermaid lines); mesh_map picks up root-level team dirs (trading-desk) and skips profiles/; enrich_teams absorbs stems into team dirs + synthetic <2-profile groups excluded; ephemeral_teams inventory (monkeypatched Path.home + fake scripts, research-swarm lane parsing, agentic lanes table); available_blueprints (3 first-class blueprints, fixtures excluded, home/templates listed); server (404, cheap /health without dashboard build, pidfile while alive, live re-render between requests); manifest coherence (plugin.yaml ⇄ schemas ⇄ handlers pinned).
- [x] Gates: `hermes plugins validate teamops --install-deps` → "Validation passed" (0 ⚠); `hermes plugins doctor teamops/` → OK, 13 tools, 0 hooks.
- [x] **Live smoke on TEMP home `/tmp/teamops-smoke`** (never live box): `python3 -m teamops.server --daemon` started (pid), /health cheap + /health?full=1 OK (2 profiles, sales team, 10 ephemeral scripts, 2 cron flags), root served 96,964-byte HTML, /nope → 404, `--stop` clean (port closed, pidfile removed). **No live leaks** — 0 teamops-* profiles in live home afterwards.
- [x] README: 13-tool surface, `teams dashboard/status/stop` quickstart + server.py module row, 70 tests.
- [x] Moved stray `google.txt` (username-probe scratch) out of the repo to the scratch cache.
- [x] Commit: 655d6ae (live dashboard server + schemas fix + fleet/cron911 enrichment).


## 2026-11-09 2.8 — morning shift (Task 2.8 web dashboard + interactive mesh graph — DONE)
- [x] **`teamops/dashboard.py`** — self-contained HTML fleet dashboard, zero external fetches (no CDN): summary cards (profiles, home crons, 24h executions, failed, dead-model count, cron flags); per-profile panel with memory bars (green/amber/red vs 2200-char cap), skills count, crons, 24h execution stats, per-job status chips; Cron911 cron-health table (SILENT GAP/failure/dead-model/disabled flags — degrades to a quiet "unavailable" line if the scan can't run); per-team kanban progress + **interactive SVG mesh graph** (circle layout, click a role to highlight its edges; inline vanilla JS).
- [x] **Wiring**: `team_status` tool gains `html`/`out_path` params (schema synced); CLI `hermes teams status --html [PATH] [--no-open]` — opens in browser by default (headless-safe), both CLI and tool return the `html_path`; default file `<home>/teamops-dashboard.html` (team-filter variants get suffixed names).
- [x] **Tests: +7 → 54 GREEN** (`tests/test_dashboard.py`): golden HTML markers from FakeHome, XSS escaping of job/detail/mesh content, bare-home degrade, team-filter filename, tool HTML path + custom out_path, headless `--html` no-crash.
- [x] Gates: `hermes plugins validate teamops --install-deps` → "Validation passed"; `hermes plugins doctor teamops/` → OK (10 tools, 0 hooks). Smoke dashboard flagged a real SILENT GAP + drew 3-node mesh correctly.
- [x] README updated (status --html quickstart, dashboard.py module row, 54 tests, cut list: mesh-graph-UI line removed — dashboard now ships it).
- [x] Post-commit dress rehearsal: full `demo-90s.sh` on temp home → all 10 steps GREEN (deploy 11/0, verify 18/0/4, zombie-cron flagged, model-scan clean, factory fixture deploy, mesh sync, rollback 11/0); then **dashboard E2E on the real deployed temp home** → `status --html` wrote 16.6KB self-contained page (13 profiles, 2 teams sales+billing-agent, 6 cron flags, mesh SVG with click JS, no external resources); rollback GREEN; **live home verified 0 teamops-* profiles (no leaks)**; temp home removed. Suite re-run: 54 GREEN + validate passed (commit 806a4d1).


## 2026-11-08 D2.7 — night shift (Task 2.7 LIVE factory lane test — SUCCESS)
- [x] ✅ **Task 2.7: LIVE factory lane test** — `agentic-coding-team.sh run --auto --lane agent-factory "3-agent lawn-care ops team"` ran to completion: Spec-Architect 21:33 → Script-Builder 21:38 → Validator 21:45 (**total ~884s / 14.7 min**). Validator handoff `onboard-script.json`: full lane gate **15/15 PASS** (bash -n ×2, roster audit, pricing $350, cron audit ×3, phantom-agent, placeholder/DEV-MODE negatives N1–N7). Generated artifacts: onboard-lawn-care.sh, verify-lawn-care.sh, MESH-MAP.md (3 edges), INBOX-dispatcher/field-agent/boss, README.md.
- [x] **Two real bugs found + fixed in the LIVE materialization path** (the D1 "fixture fallback" masked both):
  1. `_extract_workspace` token-split on spaces → failed on task ids like "3-agent lawn-care ops team-…". Fixed: rest-of-line after `task workspace:`/`Done. Workspace:` marker + ANSI strip.
  2. `blueprint_from_handoff` couldn't parse the real agent-factory schema (onboard-script.json has `files_created` absolute paths, no embedded team.yaml). Added `_blueprint_from_artifacts` converter → builds team.yaml (roles from roster), crons.yaml (from DRY_RUN_CRON dry-run lines), souls (from INBOX contract), assets/mesh.json (drop-file edges), assets/kanban.json, raw artifacts under assets/generated/. Plus `_find_handoff` disk fallback (searches ~/.hermes/ng-team/workspace new-newest, space-safe).
- [x] **Materialized live-generated blueprint**: `teamops/blueprints/lawn-care-ops/` (dispatcher → field-agent → boss, crons lawncare-co-dispatcher-daily-dispatch `0 8 * * *` / field-agent-weekly-inspection `0 9 * * 6` / boss-morning-brief `0 7 * * *`). `python3 -m teamops.cli teams list` shows it (count: 4).
- [x] **E2E on TEMP HERMES_HOME (no live touches)**: deploy lawn-care-ops 9/0 GREEN → verify 14/0 GREEN → rollback 9/0 GREEN → temp home removed. **Live home untouched** (0 teamops-* profiles).
- [x] **Model state**: ng-architect/ng-developer/ng-tech-lead pinned `deepseek/deepseek-v4-flash-0731` (nous) — lane ran with **0 dead-model flags** (model-scan confirms no ng-* flags; live home still reports the known pre-existing 51 trading LongCat 404s, out of repo scope, detect-only).
- [x] Tests: +4 regression tests (space-safe extraction, disk handoff fallback, artifacts→blueprint converter incl. artifact = destination INBOX), **47 GREEN**.
- [x] Commit: 538ff25 (canonical in HEAD history; the earlier hash 4200003 is a dangling rewritten copy — identical code, only the WORKLOG line differs)


## 2026-11-09 B0 — overnight sweep (GREEN, metadata drift fixed)
- [x] `python3 -m pytest -q` → **47 passed**; `hermes plugins doctor teamops/` → OK (10 tools, 0 hooks); `git status` clean at HEAD `e958fa0`.
- [x] Fixed WORKLOG citation: D2.7's "Commit 4200003" is a dangling (non-ancestor) hash — the feature commit live in history is `538ff25` (same message; only WORKLOG.md differs between the two trees).
- [x] Clarified blueprint count: `teams list` reports **3 first-class blueprints** (sales, property-mgmt, lawn-care-ops); the `billing-agent` fixture (own `team.yaml`) lives nested under `blueprints/fixtures/billing-agent/` — `blueprint_list` only scans top-level `team.yaml` dirs, so it's intentionally not a top-level blueprint; `teams list` is correct at 3. (count was 4 only if the fixture was counted.)
- [x] No live profiles/crons touched (test-only posture held).

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
- [ ] Task 2.10 (REVISED after boss-live audit): PORT the EXISTING vertical teams into blueprint format — sources already local: /home/idkwhoiam/onboard-insurance-claims.sh, onboard-real-estate.sh, onboard-trading.sh (+ -bull), and team-demos/insurance_claims+trading. Convert each into blueprints/<vertical>/{team.yaml,souls/,crons.yaml,assets/,verify.yaml}; use the factory lane ONLY to gap-fill missing SOULs/content, never to reinvent; Validator-gate each; commit. Note: do NOT generate new verticals from scratch — they already exist as shipped customer products (see AutomatedAgents catalog).
- [ ] Task 2.11: VPS provisioning module (OPTIONAL target) — `team_deploy --host user@host` optional SSH transport (scp blueprint + run remote install via ssh, preflight remote RAM/disk/python, remote smoke, rollback). Requires user-supplied SSH creds; clearly documented; keep local default. Tests: mock SSH runner.
- [ ] Task 2.12: Twilio phone intake module — inbound call/SMS handler wiring (twilio webhook → transcribe → intent triage → structured card → agent follow-up), mock-twilio mode for tests/demo; REAL calls blocked until Dalton provides Twilio creds via vault (do not ask in chat). Tests: mock-only.
- [ ] Freeze additions: **Nov 10 7PM EST** — after that only fixes; Nov 11 = PR final pin + video + form only.

## ACCEPTANCE RULE (boss-live 2026-11-09, user-confirmed): every optional/external feature MUST degrade gracefully — absent creds/config (VPS SSH keys, Twilio creds, fallback model pool) → feature disabled/hidden, plugin remains 100% functional, zero nags, zero broken UI. No AutomatedAgents account is ever required. Enforce in Tasks 2.9/2.11/2.12 code + tests.

## CREDS-SOURCE RULE (boss-live 2026-11-09): the plugin NEVER stores or ships credentials. Tasks 2.12/2.11 read credentials from the RUNNING USER's own environment only — Twilio = TWILIO_ACCOUNT_SID/TWILIO_AUTH_TOKEN/TWILIO_PHONE_NUMBER from the user's .env/config (no shared backend, no embedded secrets); VPS = user's own SSH key path + host config (e.g. ~/.ssh/id_ed25519). Mock/no-creds mode must always work for tests and demos.
