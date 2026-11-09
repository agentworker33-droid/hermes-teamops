"""Manifest coherence: plugin.yaml provides_tools ↔ schemas ↔ handlers ↔ CLI.

The register() loop iterates schemas.SCHEMAS and skips missing handlers
silently, so a tool declared in plugin.yaml but missing from schemas.py (or
vice versa) is a real bug — the LLM would see a tool that does not exist or
never see a tool the plugin claims. This test pins the surface closed.
"""
from pathlib import Path

import yaml

from teamops import schemas, tools

PLUGIN_YAML = Path(__file__).resolve().parent.parent / "teamops" / "plugin.yaml"


def _declared_tools() -> set[str]:
    doc = yaml.safe_load(PLUGIN_YAML.read_text())
    return set(doc.get("provides_tools") or [])


def test_every_declared_tool_has_schema_and_handler():
    declared = _declared_tools()
    assert len(declared) >= 10
    missing_schema = declared - set(schemas.SCHEMAS)
    assert not missing_schema, f"declared but unschematized: {missing_schema}"
    missing_handler = declared - {n for n in dir(tools) if callable(getattr(tools, n, None))}
    assert not missing_handler, f"declared but handlerless: {missing_handler}"


def test_every_schema_tool_is_declared():
    undeclared = set(schemas.SCHEMAS) - _declared_tools()
    assert not undeclared, f"schematized but undeclared in plugin.yaml: {undeclared}"


def test_schemas_have_valid_json_schema_shapes():
    for name, spec in schemas.SCHEMAS.items():
        assert spec["schema"]["type"] == "object", name
        assert "properties" in spec["schema"], name
