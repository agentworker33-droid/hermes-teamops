#!/usr/bin/env bash
# verify-lawn-care.sh
#
# Lane-gate executor for the 3-agent lawn-care ops team.
# Prints one PASS/FAIL line per check plus a final SUMMARY line.
# Exit code 0 iff every positive check (C1-C8) passes and every negative
# fixture (N1-N7) fires its detector.
#
# Usage:
#   bash verify-lawn-care.sh        # run from anywhere; resolves its own dir

set -u

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ONBOARD="$ROOT/onboard-lawn-care.sh"
SELF="$ROOT/verify-lawn-care.sh"

TMP="$(mktemp -d "${TMPDIR:-/tmp}/lawn-care-verify.XXXXXX")"
trap 'rm -rf "$TMP"' EXIT

PASS_COUNT=0
FAIL_COUNT=0

run_check() {
  local name="$1"
  shift
  if "$@" >/dev/null 2>&1; then
    echo "PASS $name"
    PASS_COUNT=$((PASS_COUNT + 1))
  else
    echo "FAIL $name"
    FAIL_COUNT=$((FAIL_COUNT + 1))
  fi
}

# expect_fail runs an audit function and treats a non-zero return
# (detector fired) as the passing outcome for a negative fixture.
expect_fail() {
  if "$@" >/dev/null 2>&1; then
    return 1
  fi
  return 0
}

# ---------------------------------------------------------------------------
# State capture (ground truth comes from the deploy script itself).
# ---------------------------------------------------------------------------
CUSTOMER="${CUSTOMER:-LawnCare Co}"
SLUG="$(printf '%s' "$CUSTOMER" | tr 'A-Z' 'a-z' | sed -e 's/ /-/g' -e 's/[^a-z0-9-]//g')"

bash "$ONBOARD" --show-roster > "$TMP/roster.txt" 2>/dev/null
bash "$ONBOARD" --show-pricing > "$TMP/pricing.txt" 2>/dev/null

# Canary mock: if the deploy script ever invoked HERMES_BIN during dry-run,
# a "MOCK:" line would land in the captured stdout and fail C5.
MOCK="$TMP/mock-hermes.sh"
printf '#!/usr/bin/env bash\necho "MOCK: $*"\nexit 0\n' > "$MOCK"
chmod +x "$MOCK"

mkdir -p "$TMP/deploy"
HAS_MOCK="0"
DRY_RUN_RC="0"
LAWN_CARE_DRY_RUN=1 LAWN_CARE_DRY_RUN_DIR="$TMP/deploy" CUSTOMER="$CUSTOMER" HERMES_BIN="$MOCK" \
  bash "$ONBOARD" > "$TMP/deploy-stdout.txt" 2>&1
DRY_RUN_RC=$?
if grep -q 'MOCK:' "$TMP/deploy-stdout.txt"; then
  HAS_MOCK="1"
fi
grep '^DRY_RUN_CRON ' "$TMP/deploy-stdout.txt" | sed 's/^DRY_RUN_CRON //' > "$TMP/cron.txt" || true

# ---------------------------------------------------------------------------
# Audit functions. All are parameterized over state files so negative
# fixtures can mutate copies and prove the detectors fire.
# ---------------------------------------------------------------------------
check_syntax() { bash -n "$1"; }

check_c3_roster() {
  local roster_file="$1"
  [ "$(wc -l < "$roster_file")" -eq 3 ] || return 1
  local rids agents
  rids="$(awk '{print $1}' "$roster_file" | sort)"
  [ "$rids" = "$(printf 'R1\nR2\nR3\n')" ] || return 1
  [ -z "$(awk '{print $1}' "$roster_file" | sort | uniq -d)" ] || return 1
  agents="$(awk '{print $2}' "$roster_file" | sort)"
  [ "$agents" = "$(printf 'boss\ndispatcher\nfield-agent\n')" ] || return 1
  return 0
}

check_c4_pricing() {
  local cnt src show_pricing
  cnt="$(grep -cE '^PLAN_PRICE=[0-9]+$' "$ONBOARD")"
  [ "$cnt" -eq 1 ] || return 1
  src="$(grep -oE '^PLAN_PRICE=[0-9]+$' "$ONBOARD" | grep -oE '[0-9]+')"
  [ "$src" = "350" ] || return 1
  show_pricing="$(cat "$TMP/pricing.txt")"
  [ "$show_pricing" = "350" ] || return 1
  [ -f "$TMP/deploy/README.md" ] || return 1
  grep -q '350' "$TMP/deploy/README.md" || return 1
  grep -q '\$350/mo' "$TMP/deploy/README.md" || return 1
  grep -q '3-agent starter tier' "$TMP/deploy/README.md" || return 1
  return 0
}

check_c5_cron() {
  local cron_file="$1"
  [ "$DRY_RUN_RC" -eq 0 ] || return 1
  [ "$HAS_MOCK" = "0" ] || return 1
  [ "$(wc -l < "$cron_file")" -eq 3 ] || return 1
  local actual expected
  actual="$(cut -d'|' -f2 "$cron_file" | sort)"
  expected="$(printf '0 7 * * *\n0 8 * * *\n0 9 * * 6\n')"
  [ "$actual" = "$expected" ] || return 1
  [ -z "$(cut -d'|' -f1 "$cron_file" | sort | uniq -d)" ] || return 1
  [ -z "$(cut -d'|' -f2 "$cron_file" | sort | uniq -d)" ] || return 1
  local name sched agent suffix want_sched
  while IFS='|' read -r name sched agent; do
    name="${name#DRY_RUN_CRON }"
    case "$agent" in
      dispatcher)  suffix="daily-dispatch";    want_sched="0 8 * * *" ;;
      field-agent) suffix="weekly-inspection"; want_sched="0 9 * * 6" ;;
      boss)        suffix="morning-brief";     want_sched="0 7 * * *" ;;
      *) return 1 ;;
    esac
    [ "$name" = "${SLUG}-${agent}-${suffix}" ] || return 1
    [ "$sched" = "$want_sched" ] || return 1
  done < "$cron_file"
  return 0
}

check_c6_phantom() {
  local dir="$1" roster_file="$2" cron_file="$3"
  local rid agent n
  while read -r rid agent; do
    n="$(ls "$dir/INBOX-${agent}.md" 2>/dev/null | wc -l)"
    [ "$n" -eq 1 ] || return 1
    n="$(awk -F'|' -v a="$agent" '$3==a {c++} END {print c+0}' "$cron_file")"
    [ "$n" -eq 1 ] || return 1
  done < "$roster_file"

  local f base ag
  for f in "$dir"/INBOX-*.md; do
    [ -e "$f" ] || continue
    base="$(basename "$f")"
    ag="${base#INBOX-}"
    ag="${ag%.md}"
    if ! grep -Eq "^[^ ]+ ${ag}$" "$roster_file"; then
      return 1
    fi
  done

  local name sched ag2
  while IFS='|' read -r name sched ag2; do
    if ! grep -Eq "^[^ ]+ ${ag2}$" "$roster_file"; then
      return 1
    fi
  done < "$cron_file"

  return 0
}

check_c7_tokens() {
  ! grep -qE '__[A-Z_]+__' "$1"
}

check_c8_exclusions() {
  ! grep -qE 'proving-ground-gate|jobcard\.yaml|broker\.py|TWILIO_PHONE|systemctl|omh-|mesh-orchestrator\.sh' "$1"
}

# ---------------------------------------------------------------------------
# C1-C8: positive checks.
# ---------------------------------------------------------------------------
run_check "C1 bash -n onboard-lawn-care.sh" check_syntax "$ONBOARD"
run_check "C2 bash -n verify-lawn-care.sh" check_syntax "$SELF"
run_check "C3 roster audit (exactly R1/R2/R3, each unique, agent names match)" check_c3_roster "$TMP/roster.txt"
run_check "C4 pricing audit (PLAN_PRICE=350 == --show-pricing == README)" check_c4_pricing
run_check "C5 cron audit (3 distinct names/schedules, fixed matrix, no mock invocation)" check_c5_cron "$TMP/cron.txt"
run_check "C6 phantom-agent audit (roster<->cron<->INBOX, bidirectional)" check_c6_phantom "$TMP/deploy" "$TMP/roster.txt" "$TMP/cron.txt"
run_check "C7 no placeholder tokens in deploy script" check_c7_tokens "$ONBOARD"
run_check "C8 no DEV MODE exclusions in deploy script" check_c8_exclusions "$ONBOARD"

# ---------------------------------------------------------------------------
# N1-N7: negative fixtures. Each mutates a COPY of captured state and
# asserts the corresponding detector fires (audit must FAIL on the fault).
# ---------------------------------------------------------------------------

# N1: a roster agent's INBOX is missing -> C6 must fail.
cp -r "$TMP/deploy" "$TMP/fix-n1"
rm -f "$TMP/fix-n1/INBOX-field-agent.md"
run_check "N1 missing INBOX detected" expect_fail check_c6_phantom "$TMP/fix-n1" "$TMP/roster.txt" "$TMP/cron.txt"

# N2: a ghost INBOX for a non-roster agent -> C6 must fail.
cp -r "$TMP/deploy" "$TMP/fix-n2"
touch "$TMP/fix-n2/INBOX-ghost.md"
run_check "N2 ghost INBOX detected" expect_fail check_c6_phantom "$TMP/fix-n2" "$TMP/roster.txt" "$TMP/cron.txt"

# N3: a ghost cron line for a non-roster agent -> C5 or C6 must fail.
cp "$TMP/cron.txt" "$TMP/fix-n3-cron.txt"
printf 'DRY_RUN_CRON %s-ghost-scout|0 6 * * *|ghost\n' "$SLUG" >> "$TMP/fix-n3-cron.txt"
n3_check() {
  check_c5_cron "$1"
  local rc5=$?
  check_c6_phantom "$TMP/deploy" "$TMP/roster.txt" "$1"
  local rc6=$?
  [ "$rc5" -ne 0 ] || [ "$rc6" -ne 0 ]
}
run_check "N3 ghost cron detected" n3_check "$TMP/fix-n3-cron.txt"

# N4: two crons share a schedule -> C5 must fail.
sed 's/|0 9 \* \* 6|/|0 8 * * *|/' "$TMP/cron.txt" > "$TMP/fix-n4-cron.txt"
run_check "N4 duplicate cron schedule detected" expect_fail check_c5_cron "$TMP/fix-n4-cron.txt"

# N5: duplicate R# in the roster -> C3 must fail.
cp "$TMP/roster.txt" "$TMP/fix-n5-roster.txt"
printf 'R1 boss\n' >> "$TMP/fix-n5-roster.txt"
run_check "N5 duplicate R# detected" expect_fail check_c3_roster "$TMP/fix-n5-roster.txt"

# N6: a placeholder token planted in the deploy script -> C7 must fail.
cp "$ONBOARD" "$TMP/fix-n6-onboard.sh"
printf '\n# planted placeholder\n__TOKEN__\n' >> "$TMP/fix-n6-onboard.sh"
run_check "N6 placeholder token detected" expect_fail check_c7_tokens "$TMP/fix-n6-onboard.sh"

# N7: a DEV MODE exclusion planted in the deploy script -> C8 must fail.
cp "$ONBOARD" "$TMP/fix-n7-onboard.sh"
printf '\n# planted exclusion\nsystemctl status lawn-mower-cron\n' >> "$TMP/fix-n7-onboard.sh"
run_check "N7 DEV MODE exclusion detected" expect_fail check_c8_exclusions "$TMP/fix-n7-onboard.sh"

# ---------------------------------------------------------------------------
# Summary + exit code.
# ---------------------------------------------------------------------------
echo "SUMMARY: ${PASS_COUNT} passed, ${FAIL_COUNT} failed"
if [ "$FAIL_COUNT" -eq 0 ]; then
  exit 0
fi
exit 1
