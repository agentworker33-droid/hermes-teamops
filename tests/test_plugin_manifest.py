"""Manifest coherence: plugin.yaml provides_tools ↔ schemas ↔ handlers ↔ CLI.

The register() loop iterates schemas.SCHEMAS and skips missing handlers
silently, so a tool declared in plugin.yaml but missing from schemas.py (or
vice versa) is a real bug — the LLM would see a tool that does not exist or
never see a tool the plugin claims. This test pins the surface closed.

Catalog review #1: every schema is the OpenAI function shape
{"name", "description", "parameters"}; every handler accepts the Hermes
dict-args call (`handler({"name": "sales"})`).
"""
import json
from pathlib import Path

import pytest
import yaml

from teamops import schemas, tools

PLUGIN_YAML = Path(__file__).resolve().parent.parent / "teamops" / "plugin.yaml"


def _declared_tools() -> set[str]:
    doc = yaml.safe_load(PLUGIN_YAML.read_text())
    return set(doc.get("provides_tools") or [])


def test_every_declared_tool_has_schema_and_handler():
    declared = _declared_tools()
    assert len(declared) >= 16
    missing_schema = declared - set(schemas.SCHEMAS)
    assert not missing_schema, f"declared but unschematized: {missing_schema}"
    missing_handler = declared - {n for n in dir(tools) if callable(getattr(tools, n, None))}
    assert not missing_handler, f"declared but handlerless: {missing_handler}"


def test_every_schema_tool_is_declared():
    undeclared = set(schemas.SCHEMAS) - _declared_tools()
    assert not undeclared, f"schematized but undeclared in plugin.yaml: {undeclared}"


def test_schemas_are_openai_function_shape():
    """Review #1: each schema has name/description/parameters (OpenAI shape),
    and parameters is a JSON object schema."""
    for name, spec in schemas.SCHEMAS.items():
        schema = spec["schema"]
        assert schema["name"] == name, name
        assert schema.get("description"), name
        params = schema.get("parameters")
        assert params and params.get("type") == "object", name
        assert "properties" in params, name


def test_schemas_do_not_expose_path_args():
    """Review #6: no home/jobs_path/db_path/blueprints_root/path in tool schemas."""
    forbidden = {"home", "jobs_path", "db_path", "blueprints_root", "path"}
    for name, spec in schemas.SCHEMAS.items():
        params = spec["schema"].get("parameters", {})
        props = params.get("properties", {})
        leaked = forbidden & set(props)
        assert not leaked, f"{name} exposes path args in schema: {leaked}"


@pytest.fixture()
def hermetic_args(tmp_path, monkeypatch):
    """A sample args dict per tool that never touches the live home and never
    spawns a real dashboard server."""
    (tmp_path / "home").mkdir(parents=True, exist_ok=True)
    # stub the dashboard server module so the dict-call test cannot spawn a daemon
    import teamops.server as server_mod
    monkeypatch.setattr(server_mod, "running", lambda home: None)
    monkeypatch.setattr(server_mod, "start_daemon",
                        lambda home, port, open_browser: {"ok": False, "error": "stubbed"})
    monkeypatch.setattr(server_mod, "status", lambda home: {"running": False})
    monkeypatch.setattr(server_mod, "stop", lambda home: {"running": False})
    return {
        "home": str(tmp_path / "home"),
        "blueprint_list": {},
        "team_deploy": {"name": "sales", "dry_run": True, "home": str(tmp_path / "home")},
        "team_verify": {"name": "sales", "home": str(tmp_path / "home")},
        "team_rollback": {"name": "sales", "home": str(tmp_path / "home")},
        "team_factory": {"spec": "add a billing agent to sales", "use_fixture": True,
                         "home": str(tmp_path / "home")},
        "team_status": {"home": str(tmp_path / "home")},
        "cron_scan": {"home": str(tmp_path / "home")},
        "model_scan": {"home": str(tmp_path / "home")},
        "model_rollback": {"home": str(tmp_path / "home")},
        "team_standup": {"hours": 24, "home": str(tmp_path / "home")},
        "mesh_sync": {"message": "hello", "home": str(tmp_path / "home")},
        "team_dashboard": {"port": 8799, "open_browser": False,
                           "home": str(tmp_path / "home")},
        "team_dashboard_status": {"home": str(tmp_path / "home")},
        "team_dashboard_stop": {"home": str(tmp_path / "home")},
        "phone_intake": {"mock": True, "home": str(tmp_path / "home")},
        "twilio_status": {"home": str(tmp_path / "home")},
    }


def test_handlers_accept_hermes_dict_call(hermetic_args):
    """Review #1: every handler works the way Hermes calls it —
    handler({"name": "sales"}) and returns JSON."""
    for name in schemas.SCHEMAS:
        handler = getattr(tools, name)
        out = handler(hermetic_args.get(name, {}))
        assert isinstance(out, str), name
        parsed = json.loads(out)
        assert "success" in parsed, name


def test_owner_actions_refused_in_cron_worker(hermetic_args, monkeypatch):
    """Review #4: team_deploy / team_rollback / factory-deploy refuse inside a
    cron worker process (no human to approve)."""
    monkeypatch.setenv("_HERMES_CRON_EXTERNAL_WORKER", "exec-123")
    out = json.loads(tools.team_deploy(hermetic_args["team_deploy"]))
    assert out["success"] is False and "OWNER ACTION" in out["error"]
    out = json.loads(tools.team_rollback(hermetic_args["team_rollback"]))
    assert out["success"] is False and "OWNER ACTION" in out["error"]
    out = json.loads(tools.team_factory({**hermetic_args["team_factory"], "deploy": True}))
    assert out["success"] is False and "OWNER ACTION" in out["error"]
    # plain generation (no deploy) still works
    out = json.loads(tools.team_factory(hermetic_args["team_factory"]))
    assert out["success"] is True


def test_pre_tool_call_hook_approves_owner_tools():
    """Review #4: the hook returns an approve directive for owner actions and
    None (no directive) for read-only tools."""
    from teamops import _on_pre_tool_call
    for tool in ("team_deploy", "team_rollback"):
        d = _on_pre_tool_call(tool_name=tool, args={"name": "sales"})
        assert d and d["action"] == "approve" and "rule_key" in d
    d = _on_pre_tool_call(tool_name="team_factory", args={"deploy": True})
    assert d and d["action"] == "approve"
    d = _on_pre_tool_call(tool_name="team_factory", args={"deploy": False})
    assert d is None
    d = _on_pre_tool_call(tool_name="team_status", args={})
    assert d is None
