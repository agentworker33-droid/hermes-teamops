"""Agent Factory wrapper.

Runs ~/agentic-coding-team.sh --lane agent-factory for a spec, then converts
the Validator output into a ready-to-ship blueprint directory.

Demo reliability (plan §1.5): pre-baked fixture lives at
``blueprints/fixtures/billing-agent/`` — the live run is the rehearsed act 2
and the fixture is the fallback if the run drifts, fails, or times out.
"""
from __future__ import annotations

import json
import os
import re
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


_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _extract_workspace(output: str) -> Path | None:
    """Locate the task workspace dir from the harness log output.

    The harness logs ``task workspace: $ws`` (and ``Done. Workspace: $ws``)
    where ``$ws`` may contain SPACES (task slugs keep spaces, e.g.
    "3-agent lawn-care ops team-…"), so we take the rest of the line after the
    marker instead of token-splitting (which breaks paths with spaces)."""
    for line in output.splitlines():
        clean = _ANSI_RE.sub("", line)
        for marker in ("task workspace:", "Done. Workspace:"):
            if marker in clean:
                rest = clean.split(marker, 1)[1].strip().strip("'\"")
                p = Path(rest).expanduser()
                if p.exists() and p.is_dir():
                    return p
    return None


def _team_workspace_roots() -> list[Path]:
    """Candidate root dirs where the harness stores task workspaces."""
    roots: list[Path] = []
    env = os.environ.get("NG_TEAM_DIR")
    if env:
        roots.append(Path(env).expanduser() / "workspace")
    roots.append(Path.home() / ".hermes" / "ng-team" / "workspace")
    return roots


def _find_handoff(workdir: Path | None) -> Path | None:
    """Locate the Validator handoff (onboard-script.json)."""
    candidates: list[Path] = []
    if workdir is not None:
        candidates += sorted(workdir.rglob("onboard-script.json"))
        candidates += sorted(workdir.rglob("stage3.json"))
    # Fallback: search the harness team workspace on disk. Task dirs may
    # contain spaces (output-based extraction can miss them), so discover the
    # newest task with a Validator handoff directly.
    for root in _team_workspace_roots():
        if not root.is_dir():
            continue
        for d in root.iterdir():
            h = d / "handoffs" / "onboard-script.json"
            if h.exists():
                candidates.append(h)
    if not candidates:
        return None
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[0]


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


def _artifact_role_hint(path: Path) -> str | None:
    """INBOX-dispatcher.md -> 'dispatcher' (lowercase slug)."""
    name = path.name
    if name.startswith("INBOX-") and name.endswith(".md"):
        return name[len("INBOX-"):-len(".md")].strip().lower()
    return None


def _parse_roster(mesh_text: str, role_hints: list[str]) -> list[tuple[str, str]]:
    """Parse 'Roster: R1 dispatcher, R2 field-agent, R3 boss' -> [(rid, role)]."""
    out: list[tuple[str, str]] = []
    m = re.search(r"Roster:\s*(.+)$", mesh_text, re.MULTILINE)
    if m:
        for part in m.group(1).split(","):
            mm = re.match(r"\s*R(\d+)\s+([a-zA-Z0-9_-]+)", part)
            if mm:
                out.append((f"R{mm.group(1)}", mm.group(2)))
    if not out:
        for i, role in enumerate(role_hints, start=1):
            out.append((f"R{i}", role))
    return out


def _parse_cron_rows(doc: dict, roster: list[tuple[str, str]]) -> list[dict]:
    """Extract cron rows from dry-run smoke output or the README cron table."""
    rows: list[tuple[str, str, str]] = []
    pat = re.compile(r"DRY_RUN_CRON\s+(\S+)\|([0-9*/,\s-]+)\|([a-zA-Z0-9_-]+)")
    for tr in doc.get("test_results", []):
        text = str(tr.get("output", ""))
        rows = pat.findall(text)
        if rows:
            break
    if not rows:
        for f in doc.get("files_created", []):
            p = Path(f)
            if p.name == "README.md" and p.exists():
                for line in p.read_text(errors="replace").splitlines():
                    m = re.match(r"\|\s*([a-z0-9-]+)\s*\|\s*([0-9*/,\s-]+?)\s*\|\s*([a-zA-Z0-9_-]+)\s*\|", line)
                    if m and m.group(1) not in ("Cron name", "---"):
                        rows.append((m.group(1), m.group(2), m.group(3)))
                break
    crons = []
    for name, schedule, role in rows:
        schedule_clean = " ".join(schedule.split()).strip()
        prompt = (f"As {role}, run the scheduled job '{name}': read your INBOX drop file, "
                  f"act on the items, and write your outputs per the MESH-MAP edge contract.")
        crons.append({"name": name, "schedule": schedule_clean, "profile": role, "prompt": prompt})
    return crons


def _parse_edges(mesh_path: Path | None) -> list[dict]:
    """Parse MESH-MAP.md edge rows: '| E1 | dispatcher -> field-agent | desc |'."""
    edges: list[dict] = []
    if mesh_path is None or not mesh_path.exists():
        return edges
    for line in mesh_path.read_text(errors="replace").splitlines():
        m = re.match(r"\|\s*E\d+\s*\|\s*([a-zA-Z0-9_-]+)\s*->\s*([a-zA-Z0-9_-]+)\s*\|(.+?)\s*\|", line)
        if not m:
            continue
        src, dst, desc = m.group(1), m.group(2), m.group(3)
        # drop-file contract: the source agent writes INTO the destination's
        # INBOX file, so the artifact is always INBOX-<dst>.md
        _ = desc  # description kept for humans; artifact derived from dst
        art = f"INBOX-{dst}.md"
        edges.append({"from": src, "to": dst, "via": "inbox-drop", "artifact": art})
    return edges


def _blueprint_from_artifacts(files_created: list[str], summary: str, name: str,
                              target_dir: Path, checks: list | None = None) -> Path:
    """Build a deployable TeamOps blueprint from agent-factory artifacts.

    The agent-factory lane writes customer onboarding artifacts
    (``onboard-*.sh``, ``verify-*.sh``, ``MESH-MAP.md``, ``INBOX-*``,
    ``README.md``) into its workspace. This converts that output into
    blueprint shape: team.yaml + crons.yaml + souls/ + assets/{mesh.json,
    kanban.json} + the raw artifacts under assets/generated/.
    """
    target_dir.mkdir(parents=True, exist_ok=True)
    gen = target_dir / "assets" / "generated"
    gen.mkdir(parents=True, exist_ok=True)

    mesh_path: Path | None = None
    readme_path: Path | None = None
    role_hints: list[str] = []
    for f in files_created:
        p = Path(f).expanduser()
        if not p.exists():
            continue
        if p.name == "MESH-MAP.md":
            mesh_path = p
        if p.name == "README.md":
            readme_path = p
        hint = _artifact_role_hint(p)
        if hint and hint not in role_hints:
            role_hints.append(hint)
        shutil.copy2(p, gen / p.name)

    mesh_text = mesh_path.read_text(errors="replace") if mesh_path else ""
    roster = _parse_roster(mesh_text, role_hints)
    readme_text = readme_path.read_text(errors="replace") if readme_path else ""
    roles = []
    for rid, role in roster:
        title = role
        if readme_text:
            m = re.search(rf"\| {re.escape(rid)} \| {re.escape(role)} \| ([^|]+) \|", readme_text)
            if m:
                title = m.group(1).strip()
        roles.append({
            "name": role,
            "title": title,
            "soul": f"souls/{role}.md",
            "toolsets": ["file", "terminal"],
        })

    team = {
        "name": name,
        "version": "0.1.0",
        "description": (summary or f"Factory-generated team: {name}")[:200],
        "roles": roles,
        "shared": {"kanban": "assets/kanban.json", "mesh": "assets/mesh.json"},
    }
    (target_dir / "team.yaml").write_text(_dict_to_yaml(team))

    crons = _parse_cron_rows({"test_results": checks or [], "files_created": files_created}, roster)
    if crons:
        (target_dir / "crons.yaml").write_text(_dict_to_yaml({"crons": crons}))

    edges = _parse_edges(mesh_path)
    shared_files = sorted({e["artifact"] for e in edges} | {f"INBOX-{r}.md" for _, r in roster})
    (target_dir / "assets" / "mesh.json").write_text(json.dumps({
        "version": "0.1",
        "edges": edges,
        "shared_files": shared_files,
    }, indent=2))

    soul_dir = target_dir / "souls"
    soul_dir.mkdir(parents=True, exist_ok=True)
    for rid, role in roster:
        inbox = gen / f"INBOX-{role}.md"
        body = inbox.read_text(errors="replace").strip() if inbox.exists() else ""
        edge_lines = [f"- {e['from']} -> {e['to']} via {e['artifact']}" for e in edges
                      if role in (e["from"], e["to"])]
        soul = (f"# SOUL — {role} ({rid})\n\n"
                f"You are the {role} agent in the '{name}' team ({rid}).\n\n"
                f"## Role\n{body or 'See MESH-MAP.md for the drop-file contract.'}\n\n"
                f"## Mesh edges\n" + ("\n".join(edge_lines) if edge_lines else "- none") + "\n")
        (soul_dir / f"{role}.md").write_text(soul)

    tasks = {}
    for i, (rid, role) in enumerate(roster, start=1):
        tasks[f"seed_{i}"] = {
            "id": f"seed_{i}",
            "title": f"First {role} shift: read INBOX-{role}.md and act per MESH-MAP",
            "assignee": role,
            "status": "TODO",
            "deliverables": "outputs per mesh edge contract",
            "notes": "Generated by Agent Factory; replace with real work at deploy.",
        }
    (target_dir / "assets" / "kanban.json").write_text(json.dumps({
        "columns": ["TODO", "IN_PROGRESS", "REVIEW", "DONE"],
        "tasks": tasks,
    }, indent=2))
    return target_dir


def blueprint_from_handoff(parsed: dict, name: str, target_dir: Path) -> Path:
    """Materialize a blueprint dir from factory output.

    Accepts either a full ``team.yaml`` structure (roles/crons/shared), a
    dictionary of files, or the real agent-factory lane schema: a list of
    artifact paths (``onboard-*.sh``, ``MESH-MAP.md``, ``INBOX-*.md``,
    ``README.md``) already written to disk — converted via
    :func:`_blueprint_from_artifacts`. Returns the created blueprint root.
    """
    bp = parsed.get("blueprint") or {}
    target_dir.mkdir(parents=True, exist_ok=True)
    # agent-factory lane schema: artifacts written to disk, no embedded team.yaml
    files_created = parsed.get("files_created") or []
    if not bp:
        abs_files = [f for f in files_created
                     if isinstance(f, str) and Path(f).expanduser().exists()]
        if abs_files:
            return _blueprint_from_artifacts(
                abs_files, parsed.get("summary", ""), name, target_dir,
                checks=parsed.get("checks", []),
            )
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
