"""Agent Factory wrapper.

Runs ~/agentic-coding-team.sh --lane agent-factory for a spec, then converts
the Validator output into a ready-to-ship blueprint directory.

Demo reliability (plan §1.5): pre-baked fixture lives at
``blueprints/fixtures/billing-agent/`` — the live run is the rehearsed act 2
and the fixture is the fallback if the run drifts, fails, or times out.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path

FACTORY_SCRIPT = Path.home() / "agentic-coding-team.sh"
FIXTURES_DIR = Path(__file__).resolve().parent / "blueprints" / "fixtures"
BLUEPRINTS_DIR = Path(__file__).resolve().parent / "blueprints"


def _run_lane(spec: str, timeout: int = 600) -> tuple[int, str, Path | None]:
    """Run the agent-factory lane in a temp workspace.

    Returns (exit_code, combined_output, workspace_dir_or_None)."""
    if not FACTORY_SCRIPT.exists():
        return 127, f"factory script missing: {FACTORY_SCRIPT}", None
    ws = Path(tempfile.mkdtemp(prefix="teamops-factory-"))
    try:
        proc = subprocess.run(
            [str(FACTORY_SCRIPT), "run", "--auto", "--lane", "agent-factory", spec],
            capture_output=True, text=True, timeout=timeout, cwd=str(ws),
        )
        out = (proc.stdout or "") + (proc.stderr or "")
        # The script logs "Workspace: $ws" / "…workspace/<task_id>" — find the dir.
        workdir = _extract_workspace(out) or ws
        return proc.returncode, out, workdir
    except subprocess.TimeoutExpired:
        return 124, "TIMEOUT running agent-factory lane", ws
    except Exception as e:  # noqa: BLE001
        return 1, f"factory run error: {e}", ws


def _extract_workspace(output: str) -> Path | None:
    for line in output.splitlines():
        if "workspace/" in line and ("workspace=" in line or "Workspace" in line or line.strip().endswith("workspace")):
            for token in line.split():
                if "workspace/" in token:
                    p = Path(token.strip("\"'")).expanduser()
                    if p.exists() and p.is_dir():
                        return p
    return None


def _find_handoff(workdir: Path) -> Path | None:
    """Locate the Validator handoff (onboard-script.json)."""
    candidates = sorted(workdir.rglob("onboard-script.json")) + sorted(workdir.rglob("stage3.json"))
    return candidates[0] if candidates else None


def _parse_handoff(handoff: Path) -> dict:
    doc = json.loads(handoff.read_text())
    out = {}
    out["summary"] = doc.get("summary", "")
    blueprint = doc.get("blueprint") or doc.get("team") or {}
    if not blueprint and "files" in doc:
        blueprint = doc.get("files", {})
    out["blueprint"] = blueprint
    out["files_created"] = doc.get("files_created", [])
    out["checks"] = doc.get("check_results", doc.get("test_results", []))
    return out


def blueprint_from_handoff(parsed: dict, name: str, target_dir: Path) -> Path:
    """Materialize a blueprint dir from factory output.

    Accepts either a full ``team.yaml`` structure (roles/crons/shared) or a
    dictionary of files. Returns the created blueprint root.
    """
    bp = parsed.get("blueprint") or {}
    target_dir.mkdir(parents=True, exist_ok=True)
    # team.yaml
    team = bp.get("team.yaml") if bp and isinstance(bp, dict) and "team.yaml" in bp else bp
    if isinstance(team, dict):
        team.setdefault("name", name)
        team.setdefault("version", "0.1.0")
        team.setdefault("description", parsed.get("summary", f"Factory-generated team: {name}")[:200])
        (target_dir / "team.yaml").write_text(_dict_to_yaml(team))
    elif isinstance(team, str):
        (target_dir / "team.yaml").write_text(team)
    else:
        raise ValueError("factory output missing a usable team.yaml structure")
    # crons
    crons = (bp.get("crons.yaml") if bp and isinstance(bp, dict) and "crons.yaml" in bp else None)
    if crons:
        (target_dir / "crons.yaml").write_text(crons if isinstance(crons, str) else _dict_to_yaml(crons))
    # souls
    souls = bp.get("souls") or {}
    soul_dir = target_dir / "souls"
    soul_dir.mkdir(parents=True, exist_ok=True)
    written = 0
    if isinstance(team, dict):
        for role in team.get("roles", []):
            rname = role.get("name")
            content = None
            if isinstance(souls, dict):
                content = souls.get(rname) or souls.get(f"{rname}.md")
            if content:
                (soul_dir / f"{rname}.md").write_text(content)
                role["soul"] = f"souls/{rname}.md"
                written += 1
    if written == 0 and isinstance(souls, dict) and souls:
        # souls keyed by filename
        for fname, content in souls.items():
            (soul_dir / fname).write_text(content)
    # assets
    assets = bp.get("assets") or {}
    if assets:
        asset_dir = target_dir / "assets"
        asset_dir.mkdir(parents=True, exist_ok=True)
        for fname, content in assets.items():
            (asset_dir / fname).write_text(content if isinstance(content, str) else json.dumps(content, indent=2))
    # re-write team.yaml so role soul paths stick
    if isinstance(team, dict):
        (target_dir / "team.yaml").write_text(_dict_to_yaml(team))
    return target_dir


def _dict_to_yaml(data: dict) -> str:
    try:
        import yaml
        return yaml.safe_dump(data, sort_keys=False, allow_unicode=True)
    except ImportError:  # pragma: no cover
        return json.dumps(data, indent=2)


def generate_blueprint(spec: str, name: str | None = None, use_fixture: bool = False,
                       timeout: int = 600) -> tuple[Path, dict]:
    """Generate a blueprint for a spec.

    Returns (blueprint_root, meta) where meta has 'source' ("live"|"fixture").
    Falls back to the pre-baked fixture when the live lane fails/drifts.
    """
    slug = (name or spec.lower().replace(" ", "-")).strip("-")[:40] or "generated"
    fixture = FIXTURES_DIR / slug
    if use_fixture or not FACTORY_SCRIPT.exists():
        if fixture.exists():
            return fixture, {"source": "fixture", "spec": spec, "note": "live factory skipped" if not FACTORY_SCRIPT.exists() else "fixture requested"}
        # try any fixture as fallback
        if FIXTURES_DIR.is_dir():
            for d in sorted(FIXTURES_DIR.iterdir()):
                if d.is_dir() and (d / "team.yaml").exists():
                    return d, {"source": "fixture", "spec": spec, "note": f"nearest fixture {d.name}"}
        raise FileNotFoundError(f"no pre-baked fixture for {slug} and factory script unavailable")
    code, out, workdir = _run_lane(spec, timeout=timeout)
    if code != 0:
        if fixture.exists():
            return fixture, {"source": "fixture", "spec": spec, "note": f"live lane failed (exit {code}); fixture fallback"}
        raise RuntimeError(f"agent-factory failed (exit {code}): {out[:300]}")
    handoff = _find_handoff(workdir) if workdir else None
    if handoff is None:
        if fixture.exists():
            return fixture, {"source": "fixture", "spec": spec, "note": "no Validator handoff found; fixture fallback"}
        raise FileNotFoundError(f"no Validator handoff JSON in {workdir}")
    parsed = _parse_handoff(handoff)
    target = BLUEPRINTS_DIR / slug
    try:
        blueprint_from_handoff(parsed, slug, target)
        return target, {"source": "live", "spec": spec, "summary": parsed.get("summary", ""),
                        "checks": parsed.get("checks", [])}
    except (ValueError, KeyError, TypeError) as e:  # noqa: BLE001
        if fixture.exists():
            return fixture, {"source": "fixture", "spec": spec, "note": f"handoff unshippable ({e}); fixture fallback"}
        raise RuntimeError(f"could not materialize blueprint from factory output: {e}") from e


def bake_fixture_from(name: str, target_name: str | None = None, bluemap: dict | None = None) -> Path:
    """Copy an existing blueprint into fixtures/ (used to pre-bake the demo fixture)."""
    src = BLUEPRINTS_DIR / name
    dst = FIXTURES_DIR / (target_name or name)
    if not (src / "team.yaml").exists():
        raise FileNotFoundError(f"no blueprint {name}")
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst)
    return dst
