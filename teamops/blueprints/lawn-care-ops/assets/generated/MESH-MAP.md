# LawnCare Co - MESH-MAP (3-agent lawn-care ops team)

Slug: lawncare-co
Roster: R1 dispatcher, R2 field-agent, R3 boss
Plan: $350/mo (AutomatedAgents 3-agent starter tier)

## Edges

| E1 | dispatcher -> field-agent | Daily dispatch: read INBOX-dispatcher.md, write work-order assignments into INBOX-field-agent.md |
| E2 | field-agent -> boss | Weekly inspection: read INBOX-field-agent.md, write ops report and signals into INBOX-boss.md |
| E3 | boss -> dispatcher | Morning brief: read INBOX-boss.md, write priorities and escalations into INBOX-dispatcher.md |

## Drop-file contract

One INBOX drop file per roster agent, referenced by the edges above:

- INBOX-dispatcher.md - R1 dispatcher
- INBOX-field-agent.md - R2 field-agent
- INBOX-boss.md - R3 boss
