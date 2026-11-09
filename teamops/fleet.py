"""Fleet read: profiles, per-profile cron stores, executions, memory, mesh.

Reads are HERMES_HOME-relative (never hardcode ~/.hermes). Executions come
from the AUTHORITATIVE ``cron/executions.db`` per profile (plus the home-level
store for legacy/global jobs), not the sessions DB.
"""
from __future__ import annotations

import json
import sqlite3
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


def mesh_map(home: Path) -> dict:
    """Collect team shared dirs: mesh.json + kanban.json + MESH-MAP.md."""
    teams_dir = home / "teams"
    teams = {}
    if teams_dir.is_dir():
        for td in sorted(teams_dir.iterdir()):
            if not td.is_dir():
                continue
            entry = {"dir": td.name, "mesh": {}, "kanban": None, "mesh_map": None}
            mj = td / "mesh.json"
            if mj.exists():
                try:
                    entry["mesh"] = json.loads(mj.read_text())
                except (json.JSONDecodeError, ValueError):
                    entry["mesh"] = {"parse_error": True}
            kj = td / "kanban.json"
            if kj.exists():
                try:
                    k = json.loads(kj.read_text())
                    entry["kanban"] = {"tasks": len(k.get("tasks", {})),
                                       "done": sum(1 for t in k.get("tasks", {}).values()
                                                   if str(t.get("status", "")).upper() == "DONE")}
                except (json.JSONDecodeError, ValueError):
                    entry["kanban"] = {"parse_error": True}
            mm = td / "MESH-MAP.md"
            if mm.exists():
                entry["mesh_map"] = {"lines": len(mm.read_text(errors="ignore").splitlines())}
            teams[td.name] = entry
    return teams


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
                      "enabled": j.get("enabled", True), "model": j.get("model")} for j in jobs],
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
