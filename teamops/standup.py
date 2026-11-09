"""Team standup digest: last-Nh cron outcomes per profile + open kanban work."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import fleet


def _exec_rows(db: Path, since: datetime) -> list[dict]:
    if not db.exists():
        return []
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    except sqlite3.Error:
        return []
    try:
        con.row_factory = sqlite3.Row
        rows = con.execute(
            "SELECT job_id, status, started_at, finished_at, error FROM executions WHERE "
            "COALESCE(started_at, claimed_at) >= ? ORDER BY COALESCE(started_at, claimed_at) DESC",
            (since.isoformat(),)).fetchall()
        return [dict(r) for r in rows]
    finally:
        con.close()


def _kanban_open(home: Path, team: str | None) -> list[dict]:
    """Open tasks from home kanban.json + teams/<team>/kanban.json."""
    out = []
    candidates = [home / "kanban.json"]
    teams_dir = home / "teams"
    if teams_dir.is_dir():
        for td in sorted(teams_dir.iterdir()):
            if team and team not in td.name:
                continue
            candidates.append(td / "kanban.json")
    for path in candidates:
        if not path.exists():
            continue
        try:
            doc = json.loads(path.read_text())
        except (json.JSONDecodeError, ValueError):
            continue
        for tid, task in (doc.get("tasks") or {}).items():
            status = str(task.get("status", "")).upper()
            if status != "DONE":
                out.append({"id": tid, "title": task.get("title", ""), "status": status,
                            "assignee": task.get("assignee", "")})
    return out


def digest(home: Path, team: str | None = None, hours: int = 24) -> dict:
    now = datetime.now(timezone.utc)
    since = now - timedelta(hours=hours)
    profiles = []
    for pd in fleet.profile_dirs(home):
        name = pd.name
        if team and team not in name:
            continue
        rows = _exec_rows(pd / "cron" / "executions.db", since)
        ok = sum(1 for r in rows if r.get("status") in ("completed", "ok", "success"))
        failed = [r for r in rows if r.get("status") in ("failed", "error")]
        jobs = fleet.read_jobs(pd / "cron" / "jobs.json")
        profiles.append({
            "profile": name,
            "runs_24h": len(rows),
            "ok_24h": ok,
            "failed": [{"job_id": r.get("job_id"), "error": (r.get("error") or "")[:160]} for r in failed[:5]],
            "crons": len(jobs),
        })
    home_rows = _exec_rows(fleet.executions_db_path(home, "home"), since)
    open_kanban = _kanban_open(home, team)
    total_ok = sum(p["ok_24h"] for p in profiles)
    total_failed = sum(len(p["failed"]) for p in profiles)
    return {
        "as_of": now.isoformat(),
        "hours": hours,
        "profiles": profiles,
        "home_cron_runs": len(home_rows),
        "open_kanban": open_kanban,
        "summary": (
            f"{len(profiles)} profiles · {sum(p['runs_24h'] for p in profiles) + len(home_rows)} runs "
            f"in {hours}h · {total_ok} ok · {total_failed} failed · {len(open_kanban)} open kanban"
        ),
    }
