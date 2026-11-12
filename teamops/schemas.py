"""Tool schemas for hermes-teamops — what the LLM sees.

Contract (Hermes plugin tool API): each handler is
``def handler(args: dict, **kwargs) -> str`` and reads its fields from
``args``; each schema here is the OpenAI function shape
``{"name", "description", "parameters"}``. Path-like arguments (``home``,
``jobs_path``, ``db_path``, ``blueprints_root``, ``path``) are intentionally
NOT exposed to the model — they stay CLI-only; the plugin resolves the
default HERMES_HOME itself via ``hermes_constants.get_hermes_home()``.
"""

_OWNER_TOOLS = ("team_deploy", "team_rollback", "team_factory")

_SCHEMA = {
    "blueprint_list": {
        "toolset": "teamops",
        "schema": {
            "name": "blueprint_list",
            "description": "List installed TeamOps blueprints with their versions and roles.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    "team_deploy": {
        "toolset": "teamops",
        "schema": {
            "name": "team_deploy",
            "description": "OWNER ACTION (approval required): deploy a blueprint to the local HERMES_HOME — profiles, SOULs, crons, shared kanban/mesh. Preflight checks run first; run team_verify after. Prefer the 'hermes teams deploy' CLI.",
            "parameters": {
                "type": "object",
                "required": ["name"],
                "properties": {
                    "name": {"type": "string", "description": "Blueprint name, e.g. 'sales'"},
                    "prefix": {"type": "string", "description": "Profile name prefix (default 'teamops-<team>-')"},
                    "dry_run": {"type": "boolean", "description": "Print plan without creating anything"},
                    "host": {"type": "string", "description": "Optional VPS target 'user@host[:port]' — deploys REMOTELY over ssh using the running user's own ssh credentials (agent / ~/.ssh / --ssh_key). Omit for the default local deploy."},
                    "ssh_key": {"type": "string", "description": "Optional path to the user's own ssh private key for --host (default: ssh-agent / ~/.ssh defaults)"},
                    "remote_home": {"type": "string", "description": "Remote HERMES_HOME for --host (default '~/.hermes' on the target)"},
                },
            },
        },
    },
    "team_verify": {
        "toolset": "teamops",
        "schema": {
            "name": "team_verify",
            "description": "Run Deploy Guard smoke checks on a deployed team: profile dirs, SOULs, cron stores, expected cron names, shared assets parse, optional chat probe.",
            "parameters": {
                "type": "object",
                "required": ["name"],
                "properties": {
                    "name": {"type": "string", "description": "Blueprint/team name to verify"},
                    "host": {"type": "string", "description": "Optional VPS target 'user@host[:port]' — verifies REMOTELY over ssh (see team_deploy)"},
                    "ssh_key": {"type": "string", "description": "Optional path to the user's own ssh private key for --host"},
                    "remote_home": {"type": "string", "description": "Remote HERMES_HOME for --host (default '~/.hermes' on the target)"},
                },
            },
        },
    },
    "team_rollback": {
        "toolset": "teamops",
        "schema": {
            "name": "team_rollback",
            "description": "OWNER ACTION (approval required): undo a team deploy from its snapshot — removes crons, deletes only teamops-prefixed profiles, removes only shared files under <home>/teams/<team>/. Prefer the 'hermes teams rollback' CLI.",
            "parameters": {
                "type": "object",
                "required": ["name"],
                "properties": {
                    "name": {"type": "string", "description": "Blueprint/team name to roll back"},
                    "host": {"type": "string", "description": "Optional VPS target 'user@host[:port]' — rolls back REMOTELY over ssh (see team_deploy)"},
                    "ssh_key": {"type": "string", "description": "Optional path to the user's own ssh private key for --host"},
                    "remote_home": {"type": "string", "description": "Remote HERMES_HOME for --host (default '~/.hermes' on the target)"},
                },
            },
        },
    },
    "team_factory": {
        "toolset": "teamops",
        "schema": {
            "name": "team_factory",
            "description": "Generate a blueprint from a natural-language spec using the built-in template generator (extend a base blueprint with a new role, or scaffold a new team). Optionally deploy+verify immediately — deploying is an OWNER ACTION (approval required). Prefer the 'hermes teams factory' CLI.",
            "parameters": {
                "type": "object",
                "required": ["spec"],
                "properties": {
                    "spec": {"type": "string", "description": "Natural-language spec of the team/roles to generate"},
                    "base": {"type": "string", "description": "Optional base blueprint to extend"},
                    "name": {"type": "string", "description": "Optional blueprint name override"},
                    "use_fixture": {"type": "boolean", "description": "Use the pre-baked demo fixture instead of the template generator (default false)"},
                    "deploy": {"type": "boolean", "description": "After generation, deploy + verify the new blueprint (OWNER ACTION, default false)"},
                },
            },
        },
    },
    "team_status": {
        "toolset": "teamops",
        "schema": {
            "name": "team_status",
            "description": "Fleet panel: per-profile memory %, skills, crons, 24h execution stats, Cron911 flags, per-team kanban + mesh edges. With html=true writes a self-contained dashboard HTML file and returns its path.",
            "parameters": {
                "type": "object",
                "properties": {
                    "team": {"type": "string", "description": "Optional team name filter"},
                    "html": {"type": "boolean", "description": "Write a self-contained dashboard HTML file (fleet panel + interactive mesh graph) and return its file path instead of the JSON report"},
                },
            },
        },
    },
    "cron_scan": {
        "toolset": "teamops",
        "schema": {
            "name": "cron_scan",
            "description": "Cron911: scan the home's cron jobs for silent gaps, failure streaks, never-ran jobs, dead-model flags and delivery errors (detect only).",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    "model_scan": {
        "toolset": "teamops",
        "schema": {
            "name": "model_scan",
            "description": "Model Guard: detect dead/free-gone models in pinned jobs and profile defaults, with fallback recommendations (detect + recommend only by default). swap=true is the OPT-IN auto-swap mode and requires a fallback pool.",
            "parameters": {
                "type": "object",
                "properties": {
                    "swap": {"type": "boolean", "description": "OPT-IN auto-swap mode (default false = detect + recommend only)"},
                    "pool": {"type": "array", "items": {"type": "string"}, "description": "Fallback pool 'provider/model' entries (overrides config modelguard.fallback_pool)"},
                    "dry_run": {"type": "boolean", "description": "With swap=True: plan only, write nothing (default true)"},
                },
            },
        },
    },
    "model_rollback": {
        "toolset": "teamops",
        "schema": {
            "name": "model_rollback",
            "description": "Undo model-guard swaps from the changelog (most recent, or --all).",
            "parameters": {
                "type": "object",
                "properties": {
                    "all_swaps": {"type": "boolean", "description": "Revert every recorded swap (default: most recent only)"},
                },
            },
        },
    },
    "team_standup": {
        "toolset": "teamops",
        "schema": {
            "name": "team_standup",
            "description": "Team standup digest: last-Nh cron outcomes per profile + open kanban work.",
            "parameters": {
                "type": "object",
                "properties": {
                    "team": {"type": "string", "description": "Optional team name filter"},
                    "hours": {"type": "integer", "description": "Lookback hours (default 24)"},
                },
            },
        },
    },
    "mesh_sync": {
        "toolset": "teamops",
        "schema": {
            "name": "mesh_sync",
            "description": "Broadcast a decision/announcement to team MESH-MAP.md files and the shared Mnemosyne surface (author-attributed).",
            "parameters": {
                "type": "object",
                "required": ["message"],
                "properties": {
                    "message": {"type": "string", "description": "Decision/announcement to broadcast to every team"},
                    "author": {"type": "string", "description": "Attribution label (default 'teamops')"},
                    "team": {"type": "string", "description": "Optional team filter"},
                },
            },
        },
    },
    "team_dashboard": {
        "toolset": "teamops",
        "schema": {
            "name": "team_dashboard",
            "description": "Start (or reuse) the LIVE dashboard server; data regenerates per request.",
            "parameters": {
                "type": "object",
                "properties": {
                    "port": {"type": "integer", "description": "Port (default 8781)"},
                    "open_browser": {"type": "boolean", "description": "Open the dashboard in a browser (default true)"},
                },
            },
        },
    },
    "team_dashboard_status": {
        "toolset": "teamops",
        "schema": {
            "name": "team_dashboard_status",
            "description": "Is the live dashboard server running?",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    "team_dashboard_stop": {
        "toolset": "teamops",
        "schema": {
            "name": "team_dashboard_stop",
            "description": "Stop the live dashboard server.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    "phone_intake": {
        "toolset": "teamops",
        "schema": {
            "name": "phone_intake",
            "description": "Twilio phone intake handler: inbound SMS/call payload -> structured triage card file. OPTIONAL feature, mock-first; live requires the RUNNING USER's own Twilio env via Hermes' secret scope (TWILIO_ACCOUNT_SID/AUTH_TOKEN/PHONE_NUMBER, declared optional_env). Without creds, live is a clean 'disabled' refusal; mock=true always runs the pipeline.",
            "parameters": {
                "type": "object",
                "properties": {
                    "payload": {"type": "object", "description": "Raw Twilio webhook form fields (MessageSid/CallSid, From, To, Body, CallStatus). The convenience fields below are merged in when absent."},
                    "channel": {"type": "string", "enum": ["sms", "call"], "description": "Inbound channel (default sms)"},
                    "from_number": {"type": "string", "description": "Caller's E.164 number"},
                    "to_number": {"type": "string", "description": "Twilio number messaged/called"},
                    "body": {"type": "string", "description": "SMS text body"},
                    "transcript": {"type": "string", "description": "Voice transcript (mock mode generates one for calls when omitted)"},
                    "call_status": {"type": "string", "description": "Twilio voice CallStatus (call channel)"},
                    "mock": {"type": "boolean", "description": "Force mock mode — always works with no credentials (default false = live)"},
                    "persist": {"type": "boolean", "description": "Write the structured card to the intake ledger <home>/plugin-data/teamops/intake/ (default true)"},
                },
            },
        },
    },
    "twilio_status": {
        "toolset": "teamops",
        "schema": {
            "name": "twilio_status",
            "description": "Is Twilio intake configured? (reads the RUNNING USER's env via Hermes' secret scope — declared optional_env; outputs masked values only — zero nags when disabled).",
            "parameters": {"type": "object", "properties": {}},
        },
    },
}

SCHEMAS = _SCHEMA
