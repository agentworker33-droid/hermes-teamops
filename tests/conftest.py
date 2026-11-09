"""Shared fixtures for hermes-teamops tests."""
import json
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

EXEC_SCHEMA = """
CREATE TABLE IF NOT EXISTS executions (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL,
    source TEXT NOT NULL,
    process_id TEXT NOT NULL,
    pid INTEGER NOT NULL,
    process_started_at INTEGER,
    status TEXT NOT NULL,
    claimed_at TEXT,
    started_at TEXT,
    finished_at TEXT,
    error TEXT,
    handoff_pending INTEGER,
    handoff_started_at REAL,
    delivery_outcome TEXT,
    scheduled_instant TEXT,
    progress_at TEXT
);
"""


def now_iso(offset_hours: float = 0) -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=offset_hours)).isoformat()


class FakeHome:
    """Tiny builder for a temp HERMES_HOME with jobs.json + executions.db."""

    def __init__(self, root: Path):
        self.root = root
        (root / "cron").mkdir(parents=True, exist_ok=True)
        (root / "profiles").mkdir(parents=True, exist_ok=True)
        self.jobs: list[dict] = []
        self._exec_rows: dict[str, list[tuple]] = {}  # scope -> rows

    def add_job(self, job: dict, scope: str = "home") -> "FakeHome":
        if scope == "home":
            self.jobs.append(job)
        else:
            pd = self.root / "profiles" / scope
            (pd / "cron").mkdir(parents=True, exist_ok=True)
            jp = pd / "cron" / "jobs.json"
            doc = json.loads(jp.read_text()) if jp.exists() else {"jobs": []}
            doc["jobs"].append(job)
            jp.write_text(json.dumps(doc, indent=2))
        return self

    def add_execution(self, scope: str, job_id: str, status: str = "completed",
                      started_at: str | None = None, error: str | None = None,
                      finished_at: str | None = None) -> "FakeHome":
        import uuid
        row = (
            uuid.uuid4().hex[:16], job_id, "builtin", uuid.uuid4().hex[:16], 1, 0,
            status, started_at or now_iso(), started_at or now_iso(),
            finished_at or started_at or now_iso(), error, 0, None, None, None, None,
        )
        self._exec_rows.setdefault(scope, []).append(row)
        return self

    def _write_exec_db(self, path: Path, rows: list[tuple]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(path)
        con.executescript(EXEC_SCHEMA)
        con.executemany("INSERT INTO executions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
        con.commit()
        con.close()

    def write(self) -> "FakeHome":
        (self.root / "cron" / "jobs.json").write_text(json.dumps({"jobs": self.jobs, "updated_at": now_iso()}, indent=2))
        for scope, rows in self._exec_rows.items():
            if scope == "home":
                self._write_exec_db(self.root / "cron" / "executions.db", rows)
            else:
                self._write_exec_db(self.root / "profiles" / scope / "cron" / "executions.db", rows)
        return self

    def add_kanban(self, team: str, tasks: dict[str, dict]) -> "FakeHome":
        d = self.root / "teams" / team
        d.mkdir(parents=True, exist_ok=True)
        (d / "kanban.json").write_text(json.dumps({"tasks": tasks}))
        return self

    def add_mesh(self, team: str, edges: list[dict]) -> "FakeHome":
        d = self.root / "teams" / team
        d.mkdir(parents=True, exist_ok=True)
        (d / "mesh.json").write_text(json.dumps({"edges": edges}))
        return self


@pytest.fixture()
def fake_home(tmp_path):
    """Build a FakeHome under tmp_path once per test."""
    def _make() -> FakeHome:
        return FakeHome(tmp_path / "home")
    return _make
