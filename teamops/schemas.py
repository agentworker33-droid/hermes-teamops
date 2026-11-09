"""Tool schemas for hermes-teamops — what the LLM sees."""

_SCHEMA = {
    "blueprint_list": {
        "toolset": "teamops",
        "schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Optional blueprint root dir (default: repo blueprints/)"}
            },
        },
    },
    "team_deploy": {
        "toolset": "teamops",
        "schema": {
            "type": "object",
            "required": ["name"],
            "properties": {
                "name": {"type": "string", "description": "Blueprint name, e.g. 'sales'"},
                "prefix": {"type": "string", "description": "Profile name prefix (default 'teamops-<team>-')"},
                "dry_run": {"type": "boolean", "description": "Print plan without creating anything"},
            },
        },
    },
    "team_verify": {
        "toolset": "teamops",
        "schema": {
            "type": "object",
            "required": ["name"],
            "properties": {
                "name": {"type": "string", "description": "Blueprint/team name to verify"},
            },
        },
    },
    "team_rollback": {
        "toolset": "teamops",
        "schema": {
            "type": "object",
            "required": ["name"],
            "properties": {
                "name": {"type": "string", "description": "Blueprint/team name to roll back"},
            },
        },
    },
    "team_factory": {
        "toolset": "teamops",
        "schema": {
            "type": "object",
            "required": ["spec"],
            "properties": {
                "spec": {"type": "string", "description": "Natural-language spec of the team/roles to generate"},
                "base": {"type": "string", "description": "Optional base blueprint to extend"},
            },
        },
    },
    "team_status": {
        "toolset": "teamops",
        "schema": {
            "type": "object",
            "properties": {
                "team": {"type": "string", "description": "Optional team name filter"},
                "home": {"type": "string", "description": "Optional HERMES_HOME override"},
                "html": {"type": "boolean", "description": "Write a self-contained dashboard HTML file (fleet panel + interactive mesh graph) and return its file path instead of the JSON report"},
                "out_path": {"type": "string", "description": "Output path for html=True (default: <home>/teamops-dashboard.html)"},
            },
        },
    },
    "cron_scan": {
        "toolset": "teamops",
        "schema": {
            "type": "object",
            "properties": {
                "jobs_path": {"type": "string", "description": "Optional path to jobs.json"},
                "db_path": {"type": "string", "description": "Optional path to executions.db"},
            },
        },
    },
    "model_scan": {
        "toolset": "teamops",
        "schema": {
            "type": "object",
            "properties": {
                "home": {"type": "string", "description": "Optional HERMES_HOME override"},
                "swap": {"type": "boolean", "description": "OPT-IN auto-swap mode (default false = detect + recommend only)"},
                "pool": {"type": "array", "items": {"type": "string"}, "description": "Fallback pool 'provider/model' entries (overrides config modelguard.fallback_pool)"},
                "dry_run": {"type": "boolean", "description": "With swap=True: plan only, write nothing (default true)"},
            },
        },
    },
    "model_rollback": {
        "toolset": "teamops",
        "schema": {
            "type": "object",
            "properties": {
                "home": {"type": "string", "description": "Optional HERMES_HOME override"},
                "all_swaps": {"type": "boolean", "description": "Revert every recorded swap (default: most recent only)"},
            },
        },
    },
    "team_standup": {
        "toolset": "teamops",
        "schema": {
            "type": "object",
            "properties": {
                "team": {"type": "string", "description": "Optional team name filter"},
                "hours": {"type": "integer", "description": "Lookback hours (default 24)"},
                "home": {"type": "string", "description": "Optional HERMES_HOME override"},
            },
        },
    },
    "mesh_sync": {
        "toolset": "teamops",
        "schema": {
            "type": "object",
            "required": ["message"],
            "properties": {
                "message": {"type": "string", "description": "Decision/announcement to broadcast to every team"},
                "author": {"type": "string", "description": "Attribution label (default 'teamops')"},
                "team": {"type": "string", "description": "Optional team filter"},
                "home": {"type": "string", "description": "Optional HERMES_HOME override"},
            },
        },
    },
    "team_dashboard": {
        "toolset": "teamops",
        "schema": {
            "type": "object",
            "properties": {
                "home": {"type": "string", "description": "Optional HERMES_HOME override"},
                "port": {"type": "integer", "description": "Port (default 8781)"},
                "open_browser": {"type": "boolean", "description": "Open the dashboard in a browser (default true)"},
            },
        },
    },
    "team_dashboard_status": {
        "toolset": "teamops",
        "schema": {
            "type": "object",
            "properties": {
                "home": {"type": "string", "description": "Optional HERMES_HOME override"},
            },
        },
    },
    "team_dashboard_stop": {
        "toolset": "teamops",
        "schema": {
            "type": "object",
            "properties": {
                "home": {"type": "string", "description": "Optional HERMES_HOME override"},
            },
        },
    },
}

SCHEMAS = _SCHEMA
