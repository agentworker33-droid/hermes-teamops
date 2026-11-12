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
def hermetic_home(tmp_path) -> str:
    """A scratch HERMES_HOME that no test may leak writes into the live box."""
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    return str(home)


@pytest.fixture()
def hermetic_args(hermetic_home, monkeypatch):
    """A sample args dict per tool that never touches the live home and never
    spawns a real dashboard server.

    CLI-only keys (home, out_path, path, jobs_path, db_path, verify...) are
    intentionally NOT in these dicts: reviewer round 3 #1 established that the
    model's args dict may only carry schema keys — the CLI/tests pass the
    rest through kwargs.
    """
    import teamops.server as server_mod
    monkeypatch.setattr(server_mod, "running", lambda home: None)
    monkeypatch.setattr(server_mod, "start_daemon",
                        lambda home, port, open_browser: {"ok": False, "error": "stubbed"})
    monkeypatch.setattr(server_mod, "status", lambda home: {"running": False})
    monkeypatch.setattr(server_mod, "stop", lambda home: {"running": False})
    return {
        "blueprint_list": {},
        "team_deploy": {"name": "sales", "dry_run": True},
        "team_verify": {"name": "sales"},
        "team_rollback": {"name": "sales"},
        "team_factory": {"spec": "add a billing agent to sales", "use_fixture": True},
        "team_status": {},
        "cron_scan": {},
        "model_scan": {},
        "model_rollback": {},
        "team_standup": {"hours": 24},
        "mesh_sync": {"message": "hello"},
        "team_dashboard": {"port": 8799, "open_browser": False},
        "team_dashboard_status": {},
        "team_dashboard_stop": {},
        "phone_intake": {"mock": True},
        "twilio_status": {},
    }


def _hermetic_kwargs(name: str, home: str) -> dict:
    """The kwargs the CLI would pass (CLI-only keys), per tool."""
    if name == "cron_scan":
        return {"jobs_path": home}
    return {"home": home}


def test_handlers_accept_hermes_dict_call(hermetic_args, hermetic_home):
    """Review #1: every handler works the way Hermes calls it —
    handler({"name": "sales"}) and returns JSON. CLI-only keys travel as
    kwargs (review round 3 #1)."""
    for name in schemas.SCHEMAS:
        handler = getattr(tools, name)
        out = handler(hermetic_args.get(name, {}),
                      **_hermetic_kwargs(name, hermetic_home))
        assert isinstance(out, str), name
        parsed = json.loads(out)
        assert "success" in parsed, name


def test_owner_actions_refused_in_cron_worker(hermetic_args, hermetic_home, monkeypatch):
    """Review #4: team_deploy / team_rollback / factory-deploy refuse inside a
    cron worker process (no human to approve)."""
    monkeypatch.setenv("_HERMES_CRON_EXTERNAL_WORKER", "exec-123")
    out = json.loads(tools.team_deploy(hermetic_args["team_deploy"], home=hermetic_home))
    assert out["success"] is False and "OWNER ACTION" in out["error"]
    out = json.loads(tools.team_rollback(hermetic_args["team_rollback"], home=hermetic_home))
    assert out["success"] is False and "OWNER ACTION" in out["error"]
    out = json.loads(tools.team_factory({**hermetic_args["team_factory"], "deploy": True},
                                        home=hermetic_home))
    assert out["success"] is False and "OWNER ACTION" in out["error"]
    # plain generation (no deploy) still works
    out = json.loads(tools.team_factory(hermetic_args["team_factory"], home=hermetic_home))
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


def test_pre_tool_call_hook_round2_expansion():
    """Round 2 #2: team_verify --host / model_scan swap-apply / model_rollback
    are owner actions; local verify + detect-only scans stay ungated."""
    from teamops import _on_pre_tool_call
    # team_verify: gated ONLY when host is set (remote writes)
    d = _on_pre_tool_call(tool_name="team_verify",
                          args={"name": "sales", "host": "user@vps"})
    assert d and d["action"] == "approve" and d["rule_key"] == "teamops.team_verify.host"
    assert _on_pre_tool_call(tool_name="team_verify", args={"name": "sales"}) is None
    # model_scan: gated ONLY on swap WITHOUT dry_run (apply writes configs)
    d = _on_pre_tool_call(tool_name="model_scan", args={"swap": True, "dry_run": False})
    assert d and d["action"] == "approve" and d["rule_key"] == "teamops.model_scan.swap_apply"
    assert _on_pre_tool_call(tool_name="model_scan",
                             args={"swap": True, "dry_run": True}) is None
    assert _on_pre_tool_call(tool_name="model_scan", args={"swap": False}) is None
    assert _on_pre_tool_call(tool_name="model_scan", args={}) is None
    # model_rollback: always an owner action
    d = _on_pre_tool_call(tool_name="model_rollback", args={})
    assert d and d["action"] == "approve" and d["rule_key"] == "teamops.model_rollback"


def test_owner_actions_round2_refused_in_cron_worker(hermetic_args, hermetic_home, monkeypatch):
    """Round 2 #2: the newly gated tools carry the same cron-worker refusal as
    team_deploy; read-only/locally-safe variants keep working."""
    monkeypatch.setenv("_HERMES_CRON_EXTERNAL_WORKER", "exec-123")
    # remote verify refused
    out = json.loads(tools.team_verify({**hermetic_args["team_verify"], "host": "user@vps"},
                                       home=hermetic_home))
    assert out["success"] is False and "OWNER ACTION" in out["error"]
    # local verify stays read-only and works
    out = json.loads(tools.team_verify(hermetic_args["team_verify"], home=hermetic_home))
    assert out["success"] is True
    # swap-apply refused
    out = json.loads(tools.model_scan({**hermetic_args["model_scan"],
                                       "swap": True, "dry_run": False},
                                      home=hermetic_home))
    assert out["success"] is False and "OWNER ACTION" in out["error"]
    # detect-only still works
    out = json.loads(tools.model_scan(hermetic_args["model_scan"], home=hermetic_home))
    assert out["success"] is True
    # model_rollback refused
    out = json.loads(tools.model_rollback(hermetic_args["model_rollback"], home=hermetic_home))
    assert out["success"] is False and "OWNER ACTION" in out["error"]


def test_params_schema_keys_from_args_cli_only_from_kwargs():
    """Review round 2 #4 + round 3 #1: schema keys come from args (the model's
    dict); CLI-only path keys (home, path, out_path, prefix, jobs_path,
    db_path, verify...) are accepted ONLY through kwargs."""
    merged = tools._params({"name": "sales", "home": "/etc",
                            "path": "/tmp/x", "evil": 1}, {},
                           {"name", "dry_run"}, {"home", "verify"})
    assert merged == {"name": "sales"}
    merged = tools._params({"name": "sales"}, {"home": "/tmp/h", "verify": False},
                           {"name"}, {"home", "verify"})
    assert merged == {"name": "sales", "home": "/tmp/h", "verify": False}


def test_round3_cli_only_keys_ignored_from_model_args(tmp_path, monkeypatch):
    """Review round 3 #1 (blocking): the real handlers drop CLI-only keys that
    arrive inside the model's args dict, so unapproved tools cannot overwrite
    arbitrary files or scan arbitrary paths."""
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    # team_status: out_path in args must be ignored → the dashboard lands at
    # the plugin's default path under the resolved home, never at the model's
    # arbitrary path
    out = json.loads(tools.team_status({"html": True,
                                        "out_path": str(tmp_path / "evil.html")},
                                       home=str(home)))
    assert out["success"] is True
    assert out["html_path"] != str(tmp_path / "evil.html")
    assert not (tmp_path / "evil.html").exists()
    assert "teamops-dashboard.html" in out["html_path"]
    # blueprint_list: path in args must be ignored → bundled blueprints listed
    out = json.loads(tools.blueprint_list({"path": str(tmp_path)}))
    assert out["success"] is True
    assert out["count"] >= 6
    # cron_scan: jobs_path/db_path in args must be ignored (plugin resolves
    # its own default home; read-only scan of an empty dir still returns a
    # report, proving the keys did not redirect the scan)
    monkeypatch.setattr(tools, "default_home", lambda: home)
    out = json.loads(tools.cron_scan({"jobs_path": str(tmp_path),
                                      "db_path": "/etc/passwd"}))
    assert out["success"] is True
    assert isinstance(out.get("issues"), list)
    # mesh_sync: home in args must be ignored (default_home monkeypatched to
    # tmp so no live write can happen) — the message itself still goes through
    out = json.loads(tools.mesh_sync({"message": "round3-key-drop",
                                      "home": "/etc"}))
    assert out["success"] is True


def test_round3_cli_only_keys_still_work_as_kwargs(tmp_path):
    """Review round 3 #1: the CLI/tests pass CLI-only keys as kwargs and they
    land exactly where the caller asked (team_status out_path via kwargs)."""
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    target = tmp_path / "custom-dashboard.html"
    out = json.loads(tools.team_status({"html": True}, home=str(home),
                                       out_path=str(target)))
    assert out["success"] is True
    assert out["html_path"] == str(target)
    assert target.exists()
