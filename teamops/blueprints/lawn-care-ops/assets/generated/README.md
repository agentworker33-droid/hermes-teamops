# LawnCare Co - Lawn-Care Ops Team (3-agent)

Pricing: $350/mo (AutomatedAgents 3-agent starter tier)

## Roster

| R# | Agent | Role |
| --- | --- | --- |
| R1 | dispatcher | Daily dispatch / work-order writer |
| R2 | field-agent | Weekly inspection / ops reports |
| R3 | boss | Morning brief / oversight |

## Cron jobs

| Cron name | Schedule | Agent |
| --- | --- | --- |
| lawncare-co-dispatcher-daily-dispatch | 0 8 * * * | dispatcher |
| lawncare-co-field-agent-weekly-inspection | 0 9 * * 6 | field-agent |
| lawncare-co-boss-morning-brief | 0 7 * * * | boss |

## Architecture overview

Three agents run as Hermes cron profiles and communicate through INBOX drop files described in MESH-MAP.md:

- E1 dispatcher -> field-agent: daily work-order handoff
- E2 field-agent -> boss: weekly inspection signal
- E3 boss -> dispatcher: morning-brief priorities

## Deploy

    bash onboard-lawn-care.sh

## Verify

    bash verify-lawn-care.sh
