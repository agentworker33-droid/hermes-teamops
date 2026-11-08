"""Fleet read: profiles, crons, executions, memory usage, mesh map.

D2 build shift implements team_status fully from these reads.
"""
from __future__ import annotations

from pathlib import Path


def profile_dirs(home: Path) -> list[Path]:
    d = home / "profiles"
    return sorted(p for p in d.iterdir() if p.is_dir()) if d.is_dir() else []


def jobs_path(home: Path) -> Path:
    return home / "cron" / "jobs.json"
