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
            "properties": {},
        },
    },
    "team_standup": {
        "toolset": "teamops",
        "schema": {
            "type": "object",
            "properties": {
                "team": {"type": "string", "description": "Optional team name filter"},
                "hours": {"type": "integer", "description": "Lookback hours (default 24)"},
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
            },
        },
    },
}

SCHEMAS = _SCHEMA
