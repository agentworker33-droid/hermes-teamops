#!/usr/bin/env bash
# TeamOps 90-second demo. Runs the full loop against a TEMP HERMES_HOME:
#   deploy sales -> verify -> status -> inject zombie cron -> cron-scan
#   flags it -> factory (fixture) add billing-agent -> standup -> rollback.
#
# Usage: bash teamops/demo/demo-90s.sh [--keep]
# Never touches the live ~/.hermes. Requires `hermes` CLI on PATH.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PLUGIN_SRC="$REPO_ROOT/teamops"          # the plugin package (self-contained)
HERMES_BIN="$(command -v hermes)"

KEEP=0
[[ "${1:-}" == "--keep" ]] && KEEP=1

# CRITICAL: the temp home MUST live OUTSIDE ~/.hermes. hermes resolves a
# HERMES_HOME that sits inside the default root as "profile mode" and sends
# `hermes profile create` to the native home — that would leak test profiles
# into the live box. /tmp is the safe, documented choice here.
BASE_TMP="${TEAMOPS_DEMO_TMP:-/tmp}"
TMPH="$(mktemp -d "$BASE_TMP/teamops-demo-XXXXXX")"
export HERMES_HOME="$TMPH"
mkdir -p "$HERMES_HOME/plugins"
cp -r "$PLUGIN_SRC" "$HERMES_HOME/plugins/teamops"

echo "== TeamOps demo on temp home: $TMPH =="
step() { echo; echo "### $1"; }

step "0. Install plugin into temp home + doctor"
"$HERMES_BIN" plugins doctor "$HERMES_HOME/plugins/teamops" >/dev/null && echo "doctor OK"
"$HERMES_BIN" plugins enable hermes-teamops >/dev/null && echo "plugin enabled"

step "1. hermes teams list"
"$HERMES_BIN" teams list

step "2. hermes teams deploy sales"
"$HERMES_BIN" teams deploy sales 2>&1 | tail -20

step "3. hermes teams verify sales"
"$HERMES_BIN" teams verify sales 2>&1 | tail -20

step "4. hermes teams status"
"$HERMES_BIN" teams status --home "$HERMES_HOME" 2>&1 | python3 -c "
import json, sys
r = json.load(sys.stdin)
profiles = [f\"{p['name']}(mem={p['memory']['memory_pct']}, crons={p['crons']})\" for p in r.get('profiles', [])]
print('profiles:', r.get('profile_count'), '| home_crons:', r.get('home_crons'))
for p in profiles:
    print('  ', p)
print('teams:', list(r.get('teams', {}).keys()) or 'none yet')
"

step "5. Inject zombie cron (silent gap) + hermes teams cron-scan"
python3 - "$HERMES_HOME" <<'PY'
import json, sys, datetime, os
home = sys.argv[1]
path = f"{home}/cron/jobs.json"
os.makedirs(os.path.dirname(path), exist_ok=True)
doc = json.load(open(path)) if os.path.exists(path) else {"jobs": [], "updated_at": None}
zombie = {
    "id": "zombie-demo-0001",
    "name": "Zombie Cron Fixture (silent gap)",
    "prompt": "This cron should have run daily but stopped: demo fixture.",
    "enabled": True,
    "model": "deepseek/deepseek-v4-flash-0731",
    "provider": "deepseek",
    "schedule": {"expr": "0 9 * * *", "display": "0 9 * * *"},
    "last_status": "ok",
    "last_error": None,
    "last_run_at": (datetime.datetime.now(datetime.timezone.utc) -
                    datetime.timedelta(days=5)).isoformat(),
    "failure_streak": 0,
}
doc.setdefault("jobs", []).append(zombie)
json.dump(doc, open(path, "w"), indent=2)
print(f"injected zombie cron into {path}")
PY
"$HERMES_BIN" teams cron-scan --home "$HERMES_HOME" 2>&1 | python3 -c "
import json, sys
r = json.load(sys.stdin)
print('jobs_total:', r.get('jobs_total'), 'healthy:', r.get('healthy'))
for i in r.get('issues', []):
    if 'SILENT GAP' in i.get('detail', '') or 'Zombie' in i.get('job', ''):
        print('  FLAG:', i['severity'], i['job'], '::', i['detail'][:100])
"

step "6. hermes teams model-scan"
"$HERMES_BIN" teams model-scan --home "$HERMES_HOME" 2>&1 | python3 -c "
import json, sys
r = json.load(sys.stdin)
print('dead_count:', r.get('dead_count'), 'healthy:', r.get('healthy'))
"

step "7. hermes teams factory (fixture) add billing agent --deploy"
"$HERMES_BIN" teams factory "add a billing agent to the sales team" \
    --use-fixture --deploy --home "$HERMES_HOME" 2>&1 | tail -12

step "8. hermes teams standup"
"$HERMES_BIN" teams standup --home "$HERMES_HOME" 2>&1 | python3 -c "
import json, sys
r = json.load(sys.stdin)
print(r.get('summary'))
"

step "9. hermes teams sync (mesh broadcast)"
"$HERMES_BIN" teams sync "demo: sales team switched to deepseek flash" \
    --author dalton --home "$HERMES_HOME" 2>&1 | python3 -c "
import json, sys
r = json.load(sys.stdin)
print('mesh_maps_updated:', [(e['team'], e['author']) for e in r.get('mesh_maps_updated', [])])
print('shared:', r.get('shared', {}).get('shared'))
"

step "10. hermes teams rollback sales"
"$HERMES_BIN" teams rollback sales --home "$HERMES_HOME" 2>&1 | tail -12

echo
echo "== DEMO COMPLETE =="
if [[ "$KEEP" == "1" ]]; then
  echo "Temp home kept at: $TMPH"
else
  rm -rf "$TMPH"
  echo "Temp home removed."
fi
