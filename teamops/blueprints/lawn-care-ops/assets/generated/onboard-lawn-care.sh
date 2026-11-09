#!/usr/bin/env bash
# onboard-lawn-care.sh
#
# Deploys the fixed 3-agent lawn-care ops team:
#   R1 dispatcher   - daily dispatch / work-order writer
#   R2 field-agent  - weekly inspection / ops reports
#   R3 boss         - morning brief / oversight
#
# Usage:
#   bash onboard-lawn-care.sh                      # production deploy
#   bash onboard-lawn-care.sh --show-roster        # print roster, no side effects
#   bash onboard-lawn-care.sh --show-pricing       # print price constant, no side effects
#   LAWN_CARE_DRY_RUN=1 LAWN_CARE_DRY_RUN_DIR=/tmp/out bash onboard-lawn-care.sh
#
# Env overrides:
#   CUSTOMER       business name (default: "LawnCare Co"), sanitized to slug
#   HERMES_BIN     hermes CLI (default: hermes)
#   HERMES_HOME    customer profile home (default: $HOME/.hermes); CUSTOMER_DIR wins
#   CUSTOMER_DIR   explicit customer dir; overrides HERMES_HOME

set -u

# ---------------------------------------------------------------------------
# Single source of truth: pricing constant and roster.
# Roster fields: rid|agent|cron-suffix|schedule|prompt
# ---------------------------------------------------------------------------
PLAN_PRICE=350

ROSTER_SPEC=(
  "R1|dispatcher|daily-dispatch|0 8 * * *|Daily dispatch: read INBOX-dispatcher.md, turn the days work orders into assignments, and write them into INBOX-field-agent.md"
  "R2|field-agent|weekly-inspection|0 9 * * 6|Weekly inspection: read INBOX-field-agent.md, run the inspection, write the ops report and signals into INBOX-boss.md"
  "R3|boss|morning-brief|0 7 * * *|Morning brief: read INBOX-boss.md, produce the daily brief with priorities and escalations, and drop them into INBOX-dispatcher.md"
)

# ---------------------------------------------------------------------------
# Introspection (side-effect free; run before anything else).
# ---------------------------------------------------------------------------
if [ "${1:-}" = "--show-roster" ]; then
  one_spec=""
  for one_spec in "${ROSTER_SPEC[@]}"; do
    IFS='|' read -r rid agent _rest <<< "$one_spec"
    printf '%s %s\n' "$rid" "$agent"
  done
  exit 0
fi

if [ "${1:-}" = "--show-pricing" ]; then
  printf '%s\n' "$PLAN_PRICE"
  exit 0
fi

# ---------------------------------------------------------------------------
# Customer slug (sanitized: lowercase, spaces -> dashes, strip others).
# ---------------------------------------------------------------------------
CUSTOMER="${CUSTOMER:-LawnCare Co}"
SLUG="$(printf '%s' "$CUSTOMER" | tr 'A-Z' 'a-z' | sed -e 's/ /-/g' -e 's/[^a-z0-9-]//g')"
if [ -z "$SLUG" ]; then
  echo "ERROR: CUSTOMER sanitized to an empty slug" >&2
  exit 1
fi

# ---------------------------------------------------------------------------
# Artifact writer: MESH-MAP.md, one INBOX drop file per roster agent, README.md.
# Unquoted heredocs so $CUSTOMER / $SLUG expand (no placeholder leakage).
# ---------------------------------------------------------------------------
write_artifacts() {
  local outdir="$1"

  cat > "$outdir/MESH-MAP.md" <<EOF
# ${CUSTOMER} - MESH-MAP (3-agent lawn-care ops team)

Slug: ${SLUG}
Roster: R1 dispatcher, R2 field-agent, R3 boss
Plan: \$350/mo (AutomatedAgents 3-agent starter tier)

## Edges

| E1 | dispatcher -> field-agent | Daily dispatch: read INBOX-dispatcher.md, write work-order assignments into INBOX-field-agent.md |
| E2 | field-agent -> boss | Weekly inspection: read INBOX-field-agent.md, write ops report and signals into INBOX-boss.md |
| E3 | boss -> dispatcher | Morning brief: read INBOX-boss.md, write priorities and escalations into INBOX-dispatcher.md |

## Drop-file contract

One INBOX drop file per roster agent, referenced by the edges above:

- INBOX-dispatcher.md - R1 dispatcher
- INBOX-field-agent.md - R2 field-agent
- INBOX-boss.md - R3 boss
EOF

  cat > "$outdir/INBOX-dispatcher.md" <<EOF
# INBOX - dispatcher (R1)

Drop file for the daily dispatch agent.

- The boss (R3) drops priorities and escalations here via edge E3.
- The dispatcher reads this file, turns items into work orders, and writes assignments into INBOX-field-agent.md via edge E1.
EOF

  cat > "$outdir/INBOX-field-agent.md" <<EOF
# INBOX - field-agent (R2)

Drop file for the weekly inspection agent.

- The dispatcher (R1) drops work-order assignments here via edge E1.
- The field agent reads this file, performs the weekly inspection, and writes the ops report and signals into INBOX-boss.md via edge E2.
EOF

  cat > "$outdir/INBOX-boss.md" <<EOF
# INBOX - boss (R3)

Drop file for the morning brief agent.

- The field agent (R2) drops inspection ops reports and signals here via edge E2.
- The boss reads this file, produces the morning brief, and writes priorities and escalations into INBOX-dispatcher.md via edge E3.
EOF

  cat > "$outdir/README.md" <<EOF
# ${CUSTOMER} - Lawn-Care Ops Team (3-agent)

Pricing: \$350/mo (AutomatedAgents 3-agent starter tier)

## Roster

| R# | Agent | Role |
| --- | --- | --- |
| R1 | dispatcher | Daily dispatch / work-order writer |
| R2 | field-agent | Weekly inspection / ops reports |
| R3 | boss | Morning brief / oversight |

## Cron jobs

| Cron name | Schedule | Agent |
| --- | --- | --- |
| ${SLUG}-dispatcher-daily-dispatch | 0 8 * * * | dispatcher |
| ${SLUG}-field-agent-weekly-inspection | 0 9 * * 6 | field-agent |
| ${SLUG}-boss-morning-brief | 0 7 * * * | boss |

## Architecture overview

Three agents run as Hermes cron profiles and communicate through INBOX drop files described in MESH-MAP.md:

- E1 dispatcher -> field-agent: daily work-order handoff
- E2 field-agent -> boss: weekly inspection signal
- E3 boss -> dispatcher: morning-brief priorities

## Deploy

    bash onboard-lawn-care.sh

## Verify

    bash verify-lawn-care.sh
EOF
}

# ---------------------------------------------------------------------------
# Dry-run branch (sandbox only; never invokes HERMES_BIN).
# ---------------------------------------------------------------------------
if [ "${LAWN_CARE_DRY_RUN:-0}" = "1" ]; then
  if [ -z "${LAWN_CARE_DRY_RUN_DIR:-}" ]; then
    echo "ERROR: LAWN_CARE_DRY_RUN=1 requires LAWN_CARE_DRY_RUN_DIR" >&2
    exit 1
  fi
  OUT_DIR="$LAWN_CARE_DRY_RUN_DIR"
  if ! mkdir -p "$OUT_DIR"; then
    echo "ERROR: cannot create $OUT_DIR" >&2
    exit 1
  fi
  write_artifacts "$OUT_DIR"
  for one_spec in "${ROSTER_SPEC[@]}"; do
    IFS='|' read -r rid agent suffix schedule prompt <<< "$one_spec"
    name="${SLUG}-${agent}-${suffix}"
    printf 'DRY_RUN_CRON %s|%s|%s\n' "$name" "$schedule" "$agent"
  done
  exit 0
fi

# ---------------------------------------------------------------------------
# Production deploy.
# ---------------------------------------------------------------------------
CUSTOMER_DIR="${CUSTOMER_DIR:-${HERMES_HOME:-$HOME/.hermes}}"
HERMES_BIN="${HERMES_BIN:-hermes}"

if ! mkdir -p "$CUSTOMER_DIR"; then
  echo "ERROR: cannot create $CUSTOMER_DIR" >&2
  exit 1
fi
write_artifacts "$CUSTOMER_DIR"

for one_spec in "${ROSTER_SPEC[@]}"; do
  IFS='|' read -r rid agent suffix schedule prompt <<< "$one_spec"
  name="${SLUG}-${agent}-${suffix}"
  echo "Creating cron $name (${schedule}) for $agent ..."
  if ! HERMES_HOME="$CUSTOMER_DIR" "$HERMES_BIN" --profile "$agent" cron create "$schedule" "$prompt" --name "$name"; then
    echo "ERROR: cron create failed for $name" >&2
    exit 1
  fi
done

echo "Deployed ${CUSTOMER} lawn-care ops team (roster R1/R2/R3) under $CUSTOMER_DIR"
