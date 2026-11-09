"""Fleet read: profiles, per-profile cron stores, executions, memory, mesh.

Reads are HERMES_HOME-relative (never hardcode ~/.hermes). Executions come
from the AUTHORITATIVE ``cron/executions.db`` per profile (plus the home-level
store for legacy/global jobs), not the sessions DB.
"""
from __future__ import annotations

import json
import sqlite3
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

MEMORY_CAP_CHARS = 2200  # matches the standard memory file cap shown in-session


def profile_dirs(home: Path) -> list[Path]:
    d = home / "profiles"
    if not d.is_dir():
        return []
    return sorted(p for p in d.iterdir() if p.is_dir() and not p.name.startswith("."))


def jobs_path(home: Path) -> Path:
    return home / "cron" / "jobs.json"


def profile_jobs_path(profile_dir: Path) -> Path:
    return profile_dir / "cron" / "jobs.json"


def read_jobs(path: Path) -> list[dict]:
    if not path.exists():
        return []
    try:
        doc = json.loads(path.read_text())
        return list(doc.get("jobs") or [])
    except (json.JSONDecodeError, ValueError):
        return []


def all_jobs(home: Path) -> list[tuple[str, dict]]:
    """Return (scope, job) for every job in the home: home-level + per profile."""
    out = []
    for j in read_jobs(jobs_path(home)):
        out.append(("home", j))
    for pd in profile_dirs(home):
        for j in read_jobs(profile_jobs_path(pd)):
            out.append((pd.name, j))
    return out


def _ts(s) -> datetime | None:
    if not s:
        return None
    try:
        v = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        if v.tzinfo is None:
            v = v.replace(tzinfo=timezone.utc)
        return v
    except ValueError:
        return None


def executions_db_path(home: Path, scope: str) -> Path:
    if scope == "home":
        return home / "cron" / "executions.db"
    return home / "profiles" / scope / "cron" / "executions.db"


def execution_stats(home: Path, scope: str, hours: int = 24) -> dict:
    """Summarize executions for one scope from its executions.db."""
    db = executions_db_path(home, scope)
    stats = {"total": 0, "ok": 0, "failed": 0, "recent": 0}
    if not db.exists():
        return stats
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    except sqlite3.Error:
        return stats
    try:
        con.row_factory = sqlite3.Row
        rows = con.execute("SELECT status, started_at FROM executions").fetchall()
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
        for r in rows:
            stats["total"] += 1
            st = (r["status"] or "").lower()
            if st in ("completed", "ok", "success", "delivered"):
                stats["ok"] += 1
            elif st in ("failed", "error"):
                stats["failed"] += 1
            started = _ts(r["started_at"])
            if started and started >= cutoff:
                stats["recent"] += 1
    except sqlite3.Error:
        pass
    finally:
        con.close()
    return stats


def memory_usage(profile_dir: Path) -> dict:
    """Percent-full of MEMORY.md/USER.md vs cap (chars), + skills count."""
    mem = profile_dir / "memories" / "MEMORY.md"
    user = profile_dir / "memories" / "USER.md"
    skills_dir = profile_dir / "skills"

    def _pct(p: Path) -> float | None:
        if not p.exists():
            return None
        n = len(p.read_text(errors="ignore"))
        return round(100.0 * n / MEMORY_CAP_CHARS, 1)

    skills = 0
    if skills_dir.is_dir():
        skills = sum(1 for s in skills_dir.iterdir()
                     if s.is_dir() and (s / "SKILL.md").exists())
    return {
        "memory_pct": _pct(mem),
        "user_pct": _pct(user),
        "memory_chars": len(mem.read_text(errors="ignore")) if mem.exists() else 0,
        "skills": skills,
    }


def _entry_from_dir(td: Path) -> dict:
    """Build a team entry from a directory: mesh.json → MESH-MAP.md edges,
    kanban.json counts, MESH-MAP.md line count."""
    entry = {"dir": td.name, "mesh": {}, "kanban": None, "mesh_map": None}
    mm_doc = td / "MESH-MAP.md"
    if not mm_doc.exists():
        mm_doc = td / "config" / "mesh-map.md"
    if mm_doc.exists():
        try:
            entry["mesh_map"] = {"lines": len(mm_doc.read_text(errors="ignore").splitlines())}
        except OSError:
            pass
    mj = td / "mesh.json"
    if mj.exists():
        try:
            entry["mesh"] = json.loads(mj.read_text())
        except (json.JSONDecodeError, ValueError):
            entry["mesh"] = {"parse_error": True}
    elif mm_doc.exists():
        try:
            entry["mesh"] = {"edges": parse_mesh_edges(mm_doc.read_text(errors="ignore"))}
        except OSError:
            pass
    kj = td / "kanban.json"
    if kj.exists():
        try:
            k = json.loads(kj.read_text())
            entry["kanban"] = {"tasks": len(k.get("tasks", {})),
                               "done": sum(1 for t in k.get("tasks", {}).values()
                                           if str(t.get("status", "")).upper() == "DONE")}
        except (json.JSONDecodeError, ValueError):
            entry["kanban"] = {"parse_error": True}
    return entry


def parse_mesh_edges(text: str) -> list[dict]:
    """Parse markdown mesh-edge tables ('| E1 | From → To | artifact |') into
    {from, to, via} edges. Also tolerates plain 'A --> B' mermaid lines."""
    edges: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for raw in text.splitlines():
        s = raw.strip()
        cells = []
        if s.startswith("|"):
            cells = [c.strip() for c in s.strip("|").split("|")]
        if len(cells) >= 2:
            pair = cells[1]
            via = cells[2] if len(cells) > 2 else ""
        else:
            pair = s
            via = ""
        for arrow in ("-->", "->", "→"):
            if arrow in pair:
                f, t = (x.strip() for x in pair.split(arrow, 1))
                f = f.split()[-1].strip("`")
                t = t.split()[0].strip("`,")
                if not f or not t or f == t:
                    break
                via = via.split("|")[0].strip("`")
                key = (f, t)
                if key not in seen:
                    seen.add(key)
                    edges.append({"from": f, "to": t, "via": via})
                break
    return edges


_TEAM_DIR_SKIP = {"profiles", "cron", "skills", "plugins", "scripts",
                  "memories", "teams", "templates", "backups", "desktop",
                  "desktop-plugins", "external-workers", "adm"}


def _team_markers(td: Path) -> bool:
    return (td / "MESH-MAP.md").is_file() or (td / "mesh.json").is_file() \
        or (td / "kanban.json").is_file() or (td / "config" / "mesh-map.md").is_file()


def mesh_map(home: Path) -> dict:
    """Collect team shared dirs: home/teams/* + root-level team dirs that carry
    mesh markers (MESH-MAP.md, mesh.json, kanban.json, config/mesh-map.md)."""
    teams: dict = {}
    teams_dir = home / "teams"
    if teams_dir.is_dir():
        for td in sorted(teams_dir.iterdir()):
            if not td.is_dir():
                continue
            teams[td.name] = _entry_from_dir(td)
    for td in sorted(home.iterdir()):
        if not td.is_dir() or td.name.startswith(".") or td.name in _TEAM_DIR_SKIP:
            continue
        if td.name in teams:
            continue
        if _team_markers(td):
            teams[td.name] = _entry_from_dir(td)
    return teams


def enrich_teams(teams: dict, profiles: list[dict]) -> dict:
    """Attach member profiles + aggregate stats to team entries and append
    profile-prefix groups (war, strategy, fleet, ...) that have no dir/team.

    A profile stem is covered by a team whose dir name starts with the stem
    (trading-desk covers trading-*, soc-team covers soc-*). Groups with fewer
    than 2 profiles are left out — they are roles, not teams.
    """
    out = {k: dict(v) for k, v in teams.items()}
    by_stem: dict[str, list[dict]] = {}
    for p in profiles:
        by_stem.setdefault(p["name"].split("-")[0], []).append(p)

    def _stats(members: list[dict]) -> dict:
        crons = sum(m.get("crons", 0) for m in members)
        ex = {}
        for m in members:
            e = m.get("executions_24h") or {}
            for k in ("total", "ok", "failed"):
                ex[k] = ex.get(k, 0) + (e.get(k, 0) or 0)
        mems = [(m.get("memory") or {}).get("memory_pct") for m in members]
        mems = [x for x in mems if x is not None]
        return {"members": len(members), "crons": crons,
                "exec_24h": ex, "memory_pct": round(sum(mems) / len(mems), 1) if mems else None,
                "skills": sum((m.get("memory") or {}).get("skills", 0) for m in members)}

    for tname, t in out.items():
        stem = tname.split("-")[0]
        if stem in by_stem:
            t["members"] = [p["name"] for p in by_stem[stem]]
            t["stats"] = _stats(by_stem.pop(stem))
    for stem, members in sorted(by_stem.items()):
        if len(members) < 2:
            continue
        out.setdefault(stem, {"dir": None, "mesh": {}, "kanban": None, "mesh_map": None})
        out[stem]["group"] = True
        out[stem]["members"] = [p["name"] for p in members]
        out[stem]["stats"] = _stats(members)
    return out


def _job_has_handoff(job: dict) -> bool:
    """Best-effort: does this cron's prompt hand a typed artifact to another role?"""
    prompt = str(job.get("prompt") or "")
    low = prompt.lower()
    markers = ("handoff", "handoffs/", "typed handoff", ".json to ", "deliverable to ",
               "passes ", "forwards ", "fan-in", "sends the file")
    return any(m in low for m in markers)


def _job_schedule(job: dict) -> str:
    s = job.get("schedule")
    if isinstance(s, dict):
        s = s.get("expr", "")
    return str(s or "")


def fleet_report(home: Path, team_filter: str | None = None) -> dict:
    """Full fleet snapshot for team_status."""
    profiles = []
    for pd in profile_dirs(home):
        name = pd.name
        if team_filter and team_filter not in name:
            continue
        jobs = read_jobs(profile_jobs_path(pd))
        profiles.append({
            "name": name,
            "has_soul": (pd / "SOUL.md").exists(),
            "memory": memory_usage(pd),
            "crons": len(jobs),
            "jobs": [{"name": j.get("name"), "status": j.get("last_status"),
                      "enabled": j.get("enabled", True), "model": j.get("model"),
                      "handoff": _job_has_handoff(j), "schedule": _job_schedule(j)} for j in jobs],
            "executions_24h": execution_stats(home, name, hours=24),
        })
    home_jobs = read_jobs(jobs_path(home))
    teams = mesh_map(home)
    report = {
        "home": str(home),
        "profiles": profiles,
        "profile_count": len(profiles),
        "home_crons": len(home_jobs),
        "home_executions_24h": execution_stats(home, "home", hours=24),
        "teams": teams,
    }
    if teams:
        report["graph"] = render_graph(teams)
    return report


def render_graph(teams: dict) -> str:
    """Mermaid-ish edge view from mesh.json entries."""
    lines = ["```mermaid", "graph LR"]
    for team, t in teams.items():
        edges = (t.get("mesh") or {}).get("edges", [])
        if not edges:
            continue
        lines.append(f"  subgraph {team}")
        for e in edges:
            lines.append(f"    {e.get('from')} -->|{e.get('via')}| {e.get('to')}")
        lines.append("  end")
    lines.append("```")
    return "\n".join(lines)


# Canonical one-shot / profile-backed team scripts that live in the user's home.
# (script, display label, plain-language description, roles, hub role)
EPHEMERAL_TEAMS = [
    ("research-swarm.sh", "research-swarm",
     "N-lane research team: market, competitor, jobs or custom lanes in parallel.",
     ["researcher-lane", "researcher-lane", "synthesizer"], "synthesizer"),
    ("content-engine.sh", "content-engine",
     "Content pipeline: strategist, parallel writers, critic, distributor (draft-gated).",
     ["strategist", "writer-lane", "writer-lane", "critic", "distributor", "analytics"], "strategist"),
    ("agentic-coding-team.sh", "agentic-coding",
     "18-lane coding harness: architect → tech lead → developer with typed handoffs.",
     ["architect", "tech-lead", "developer", "validator"], "architect"),
    ("soc-team.sh", "soc-team",
     "Defensive security sweep: log analyst, threat hunter, hardening auditor, incident responder.",
     ["log-analyst", "threat-hunter", "hardening-auditor", "incident-responder", "orchestrator"], "orchestrator"),
    ("pentest-team.sh", "pentest-team",
     "Authorized pentest team — scope allowlisted, agents read evidence snapshots only.",
     ["pentest-scope-guard", "pentest-recon", "pentest-vuln-scanner",
      "pentest-exploit-tester", "pentest-report-writer"], "pentest-scope-guard"),
    ("contract-review-burst.sh", "contract-review",
     "Legal review burst: clause extract → compliance → reviewer + risk flags → negotiator.",
     ["clause-extractor", "compliance-checker", "contract-reviewer", "risk-flagger",
      "negotiation-drafter", "redline-manager"], "contract-reviewer"),
    ("strategy-lab.sh", "strategy-lab",
     "Trading research: cycle locator → ideator → real backtester → skeptic veto → reporter.",
     ["cycle-locator", "strategy-ideator", "backtester", "skeptic", "reporter"], "cycle-locator"),
    ("war-room.sh", "war-room",
     "Decision committee: 5 voices in parallel, chair writes DECISION.md with kill-criteria.",
     ["analyst", "operator", "buyer", "risk", "skeptic", "chair"], "chair"),
    ("fleet-health.sh", "fleet-health",
     "Health monitor: deterministic collector writes facts, one LLM seat writes the brief.",
     ["collector", "report-seat", "deep-dive-seat"], "report-seat"),
    ("prospect-team.sh", "prospect-team",
     "Prospect pipeline: find → qualify → write. Launched by the sales-dev cron on a schedule.",
     ["prospect-finder", "prospect-qualifier", "prospect-writer", "prospect-sequencer",
      "prospect-replies", "prospect-booker", "prospect-analyst"], "prospect-finder"),
]


def _script_running(script: str) -> bool:
    """Best-effort: is a run of this script in flight right now?"""
    try:
        r = subprocess.run(["pgrep", "-f", script],
                           capture_output=True, text=True, timeout=3)
        return r.returncode == 0
    except Exception:  # noqa: BLE001
        return False


def _research_swarm_lanes(script_path: Path) -> tuple[list[str], int]:
    """Read the DEFAULT preset (competitors) from research-swarm.sh and return
    short lane slugs + the lane count actually used when the swarm runs."""
    import re as _re
    slugs: list[str] = []
    try:
        lines = script_path.read_text(errors="ignore").splitlines()
        capture = False
        for ln in lines:
            if ln.startswith("LANES_COMPETITORS=("):
                capture = True
                continue
            if capture:
                if ln.strip() == ")":
                    break
                lane = ln.strip().strip('"').strip("'").strip(",")
                if not lane:
                    continue
                words = [w for w in lane.replace(":", " ").split() if w]
                stem = "-".join(words[:2]).lower()
                stem = _re.sub(r"[^a-z0-9-]+", "-", stem).strip("-")
                stem = _re.sub(r"-{2,}", "-", stem)
                slugs.append(stem[:26] or f"lane-{len(slugs) + 1}")
    except OSError:
        pass
    if not slugs:
        slugs = [f"lane-{i}" for i in range(1, 8)]
    return slugs, len(slugs)


# agentic-coding-team.sh — the 18-lane table, grouped (verified from the script).
AGENTIC_LANES: dict[str, list[tuple[str, str, str]]] = {
    "Factory": [
        ("code", "Architect | Tech Lead | Developer", "writes/refactors code (the original flow)"),
        ("content", "Strategist | Writer | Designer", "builds sites, docs, assets (safest lane)"),
        ("agent-factory", "Spec-Architect | Script-Builder | Validator", "generates new onboard-*.sh products from templates"),
        ("migration", "Analyzer | Port-Engineer | Test-Runner", "rewrites a codebase in a new language/framework (the sellable one)"),
        ("test-harness", "Coverage-Mapper | Test-Writer | Mutator", "writes a test suite for an untested codebase"),
        ("data-wrangling", "Schema-Analyst | Cleaner | Reconciler", "cleans messy exports into a proper database"),
        ("integration", "Connector-Mapper | Wrapper-Builder | Probe-Runner", "wires SaaS tools into a team"),
        ("paper-to-production", "Paper-Reader | Implementation-Engineer | Replication-Runner", "arXiv paper in, working implementation + replication report out"),
        ("adversarial-audit", "Builder | Red-Team | Blue-Team", "one builds, one attacks, one defends"),
        ("legacy-translator", "Code-Reader | Plain-English-Drafter | Code-Fact-Checker", "explains a gnarly codebase to a non-technical owner"),
        ("incident-postmortem", "Log-Scanner | Timeline-Builder | Root-Cause-Analyst", "logs in, timeline + root cause + remediation out"),
    ],
    "Meta": [
        ("prompt-eval", "Baseline-Definer | Variant-Generator | Judge", "improves a prompt/agent via A/B variants + a held-out eval"),
        ("debug", "Reproducer | Root-Cause-Hunter | Fix-Verifier", "bug/flaky test/regression in, root cause + verified fix out"),
        ("code-review", "Diff-Reader | Critic | Maintainability-Judge", "PR/diff in, severity-scored structured review out"),
    ],
    "Design / Intel": [
        ("architecture-spike", "Requirements-Clarifier | Option-Generator | Decision-Recorder", "new feature/redesign in, ADR-style decision record out"),
        ("competitive-intel", "Fingerprinter | Comparator | Gap-Analyst", "category/competitor set in, fact-checked matrix + gaps out"),
    ],
    "Team Mirror": [
        ("soc-sweep", "Log-Scanner | Threat-Hunter | Hardening-Auditor", "infra scan in, evidence-backed security findings out"),
        ("prospect-outreach", "Prospect-Finder | Qualifier | Copy-Writer", "target list in, verified prospects + paste-ready outreach out"),
    ],
}


def ephemeral_teams(home: Path) -> list[dict]:
    """Inventory the canonical ephemeral team scripts (live at Path.home()).

    These are one-shot / profile-backed runners: zero resident RAM when idle,
    a full agent process (~460 MB) only while a run is in flight.
    """
    out = []
    scripts_dir = Path.home()
    for script, label, description, roles, hub in EPHEMERAL_TEAMS:
        p = scripts_dir / script
        if not p.exists():
            continue
        running = _script_running(script)
        entry = {
            "name": label,
            "script": script,
            "description": description,
            "roles": list(roles),
            "hub": hub,
            "path": str(p),
            "running": running,
            "status": "running" if running else "ready",
            "status_word": "RUNNING" if running else "READY",
        }
        if label == "research-swarm":
            lane_slugs, lane_count = _research_swarm_lanes(p)
            entry["roles"] = lane_slugs + ["synthesizer"]
            entry["hub"] = "synthesizer"
            entry["sub_agents"] = lane_count
            entry["description"] = (f"{lane_count} sub-researchers fan out in parallel, "
                                    f"then 1 synthesizer merges their findings — the default "
                                    f"competitors preset.")
        elif label == "agentic-coding":
            entry["lanes"] = AGENTIC_LANES
        out.append(entry)
    return out


def _blueprint_meta(d: Path) -> dict | None:
    try:
        import yaml  # lazily; yaml is present in Hermes envs
        doc = yaml.safe_load((d / "team.yaml").read_text()) or {}
        roles = [r.get("name") for r in (doc.get("roles") or [])]
        desc = (doc.get("description") or "").strip().splitlines()[0][:150]
        return {"id": d.name, "name": doc.get("name", d.name),
                "version": doc.get("version", ""), "roles": roles,
                "description": desc}
    except Exception:  # noqa: BLE001
        return None


def available_blueprints(home: Path) -> dict:
    """Deploy-ready blueprints (plugin blueprints dir) + customer vertical
    templates (home/templates) that ship via the onboard-*.sh installers."""
    bp_root = Path(__file__).resolve().parent / "blueprints"
    blueprints = []
    if bp_root.is_dir():
        for d in sorted(bp_root.iterdir()):
            if d.name == "fixtures" or not (d / "team.yaml").exists():
                continue
            meta = _blueprint_meta(d)
            if meta:
                blueprints.append(meta)
    templates = []
    tpl = home / "templates"
    if tpl.is_dir():
        templates = sorted(x.name for x in tpl.iterdir()
                           if x.is_dir() and not x.name.startswith("."))
    return {"blueprints": blueprints, "templates": templates}
