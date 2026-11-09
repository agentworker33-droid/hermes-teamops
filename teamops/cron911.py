"""Cron911: silent-gap + failure + dead-model detection over jobs.json +
executions.db (parsing ported from ~/.hermes/scripts/cron-health-check.py;
detect-only in v1 — no auto-fix, no writes)."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import fleet

MODEL_404_MARKERS = ("404", "no longer free", "free period", "requires available", "model not found")

# Expected max gap (hours) between runs per cadence, with safety margin.
CADENCE_GAP_H = {
    "subhourly": 3,
    "hourly": 3,
    "daily": 2 * 24,
    "weekdays": 4 * 24,   # MON-FRI: max gap is Fri->Mon (~3d), allow 4
    "weekly": 9 * 24,
    "monthly": 40 * 24,
    "unknown": 30 * 24,
}


def classify_cadence(expr: str) -> str:
    """Best-effort cadence from a 5-field cron expr (mirrors cron-health-check)."""
    try:
        f = (expr or "").split()
        if len(f) < 5:
            return "unknown"
        minute, hour, dom, month, dow = f[:5]
        if dom != "*":
            return "monthly" if month == "*" else "unknown"
        if dow != "*":
            if any(c in dow for c in "-,") and not dow.startswith("*/"):
                return "weekdays"
            return "weekly"
        if hour == "*" or "/" in minute:
            return "hourly" if "/" not in minute else "subhourly"
        return "daily"
    except Exception:  # noqa: BLE001
        return "unknown"


def _ts(s):
    if not s:
        return None
    try:
        v = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        if v.tzinfo is None:
            v = v.replace(tzinfo=timezone.utc)
        return v
    except ValueError:
        return None


_SEV_RANK = {"HIGH": 0, "MED": 1, "INFO": 2}


def merge_issues(issues: list[dict]) -> list[dict]:
    """Collapse multi-rule rows per (scope, job) into one row.

    Cron911 fires one issue per rule (failure_streak, last_status, last_error,
    DEAD MODEL, silent gap) — the same job can appear 3-4 times for the same
    root cause. Merge to the highest severity and compose a compact detail
    list (substring-duplicate detail strings are dropped first).
    """
    groups: dict[tuple[str, str], dict] = {}
    for i in issues:
        key = (str(i.get("scope", "")), str(i.get("job", "")))
        g = groups.setdefault(key, {"severity": "INFO", "parts": []})
        sev_cur = str(g["severity"] or "INFO")
        sev_new = str(i.get("severity") or "INFO")
        if _SEV_RANK.get(sev_new, 9) < _SEV_RANK.get(sev_cur, 9):
            g["severity"] = sev_new
        detail = i.get("detail") or ""
        cap = 120 if ("last_error" in detail and "last_status" not in detail) else 200
        g["parts"].append(detail[:cap])
    out = []
    for (scope, job), g in groups.items():
        kept, seen = [], set()
        for p in g["parts"]:
            if any(p in s or s in p for s in seen):
                continue
            seen.add(p)
            kept.append(p)
        out.append({"severity": str(g["severity"] or "INFO"), "scope": scope, "job": job,
                    "detail": " · ".join(kept)})
    out.sort(key=lambda x: (_SEV_RANK.get(x["severity"], 9), x["scope"], x["job"]))
    return out


def _exec_last_run(db: Path, job_id: str) -> datetime | None:
    if not db.exists():
        return None
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            row = con.execute("SELECT MAX(started_at) m FROM executions WHERE job_id=?", (job_id,)).fetchone()
        finally:
            con.close()
        return _ts(row[0] if row else None)
    except sqlite3.Error:
        return None


def scan(home: Path, days: int = 7, db_override: Path | None = None) -> dict:
    """Silent gaps + failures + dead models. Returns a JSON-able report.

    ``db_override`` swaps the home-scope executions DB (used by tests and by
    callers that carry their own DB path); per-profile DBs still resolve from
    the home layout.
    """
    now = datetime.now(timezone.utc)
    jobs = fleet.all_jobs(home)
    issues, info = [], []
    for scope, j in jobs:
        jid = j.get("id", "")
        name = j.get("name", jid)
        streak = j.get("failure_streak") or 0
        status = j.get("last_status")
        lerr = j.get("last_error")
        dlerr = j.get("last_delivery_error")
        enabled = j.get("enabled", True)
        if streak > 0:
            issues.append({"severity": "HIGH", "scope": scope, "job": name,
                           "detail": f"failure_streak={streak} last_status={status} last_error={lerr}"})
        if status and status not in ("ok", "completed", "success"):
            issues.append({"severity": "HIGH", "scope": scope, "job": name,
                           "detail": f"last_status={status} last_error={lerr}"})
        if lerr:
            issues.append({"severity": "MED", "scope": scope, "job": name, "detail": f"last_error: {lerr}"})
        if dlerr:
            issues.append({"severity": "MED", "scope": scope, "job": name, "detail": f"last_delivery_error: {dlerr}"})
        if not enabled:
            info.append({"job": name, "scope": scope, "detail": "DISABLED"})
        # dead-model flags (detect + recommend only)
        model = j.get("model") or ""
        if any(mk in (lerr or "").lower() for mk in MODEL_404_MARKERS):
            issues.append({"severity": "HIGH", "scope": scope, "job": name,
                           "detail": f"DEAD MODEL: {model} — {lerr[:160]}"})
        # silent gap
        if not enabled:
            continue
        expr = (j.get("schedule") or {}).get("expr") if isinstance(j.get("schedule"), dict) else j.get("schedule")
        cadence = classify_cadence(str(expr or ""))
        gap_h = CADENCE_GAP_H.get(cadence, CADENCE_GAP_H["unknown"])
        db = db_override if (scope == "home" and db_override is not None) else fleet.executions_db_path(home, scope)
        last = _exec_last_run(db, jid) or _ts(j.get("last_run_at"))
        if last is None:
            issues.append({"severity": "MED", "scope": scope, "job": name,
                           "detail": "enabled but NEVER ran (no executions, no last_run_at)"})
            continue
        age_h = (now - last).total_seconds() / 3600
        if age_h > gap_h:
            issues.append({"severity": "HIGH", "scope": scope, "job": name,
                           "detail": f"SILENT GAP: last run {last.strftime('%Y-%m-%d %H:%M')} "
                                     f"({age_h/24:.1f}d ago) but cadence={cadence} (expect <= {gap_h/24:.1f}d)"})
    issues = merge_issues(issues)
    return {
        "checked_at": now.isoformat(),
        "home": str(home),
        "jobs_total": len(jobs),
        "jobs_enabled": sum(1 for _, j in jobs if j.get("enabled", True)),
        "issues": issues,
        "info": info,
        "healthy": len(issues) == 0,
    }
