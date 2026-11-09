"""Deploy Guard tests (D1): preflight decisions, dry-run plan, smoke read,
rollback snapshot handling — all against fake runners + temp homes."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from teamops.build import Blueprint
from teamops.guard import DeployGuard

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def sales_bp() -> Blueprint:
    return Blueprint(ROOT / "teamops" / "blueprints" / "sales")


class FakeRunner:
    """Deterministic runner that simulates hermes CLI outcomes."""

    def __init__(self, profile_create_ok: bool = True):
        self.calls: list[list[str]] = []
        self.profile_create_ok = profile_create_ok

    def __call__(self, cmd, timeout=180, env=None):
        self.calls.append(cmd)
        if len(cmd) >= 4 and cmd[1] == "profile" and cmd[2] == "create":
            if not self.profile_create_ok:
                return 1, "profile create failed (simulated)"
            # simulate dir creation so smoke can find it
            name = cmd[3]
            prof_dir = Path(env.get("HERMES_HOME", "/tmp")) / "profiles" / name
            prof_dir.mkdir(parents=True, exist_ok=True)
            (prof_dir / "SOUL.md").write_text("fake soul")
            return 0, f"created {name}"
        if len(cmd) >= 3 and cmd[1] == "profile" and cmd[2] == "delete":
            return 0, "deleted"
        if len(cmd) >= 5 and cmd[1] == "-p" and cmd[3] == "cron" and cmd[4] == "create":
            profile = cmd[2]
            prof_dir = Path(env.get("HERMES_HOME", "/tmp")) / "profiles" / profile
            job_dir = prof_dir / "cron"
            job_dir.mkdir(parents=True, exist_ok=True)
            jobs = job_dir / "jobs.json"
            existing = json.loads(jobs.read_text()) if jobs.exists() else {"jobs": []}
            existing["jobs"].append({"name": cmd[cmd.index("--name") + 1], "id": f"job-{len(existing['jobs']) + 1}"})
            jobs.write_text(json.dumps(existing))
            return 0, "job created"
        if len(cmd) >= 5 and cmd[1] == "-p" and cmd[3] == "cron" and cmd[4] == "remove":
            # remove by id: delete the matching job
            profile = cmd[2]
            job_id = cmd[5]
            job_dir = Path(env.get("HERMES_HOME", "/tmp")) / "profiles" / profile / "cron"
            jobs_file = job_dir / "jobs.json"
            if jobs_file.exists():
                doc = json.loads(jobs_file.read_text())
                doc["jobs"] = [j for j in doc.get("jobs", []) if j.get("id") != job_id]
                jobs_file.write_text(json.dumps(doc))
            return 0, "job removed"
        if len(cmd) >= 3 and cmd[1] == "cron" and cmd[2] == "create":
            # global cron; put it in home cron dir for smoke
            home = Path(env.get("HERMES_HOME", "/tmp"))
            job_dir = home / "cron"
            job_dir.mkdir(parents=True, exist_ok=True)
            jobs = job_dir / "jobs.json"
            existing = json.loads(jobs.read_text()) if jobs.exists() else {"jobs": []}
            existing["jobs"].append({"name": cmd[cmd.index("--name") + 1]})
            jobs.write_text(json.dumps(existing))
            return 0, "job created"
        if len(cmd) >= 3 and cmd[1] == "profile" and cmd[2] == "list":
            return 0, "[]"
        return 0, "ok"


def test_preflight_green_with_fake_runner(tmp_path, sales_bp, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))  # tmp_path IS the default root → isolation OK
    guard = DeployGuard(sales_bp, home=tmp_path)
    guard.runner = FakeRunner()
    results = guard.preflight()
    statuses = {r.name: r.status for r in results}
    # no name collision because temp home is empty
    assert statuses["name-collision"] == "PASS"
    assert statuses["disk"] == "PASS"
    assert all(r.status != "FAIL" for r in results)


def test_preflight_detects_profile_collision(tmp_path, sales_bp, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    prof = tmp_path / "profiles" / "teamops-sales-boss"
    prof.mkdir(parents=True)
    guard = DeployGuard(sales_bp, home=tmp_path)
    guard.runner = FakeRunner()
    results = guard.preflight()
    by_name = {r.name: r.status for r in results}
    assert by_name["name-collision"] == "FAIL"


def test_preflight_flags_home_inside_default_root(tmp_path, sales_bp, monkeypatch):
    """A target home strictly inside the default hermes root must FAIL: hermes
    profile create would resolve to the native home and leak test profiles."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    inside = tmp_path / "sub" / "home"
    inside.mkdir(parents=True)
    guard = DeployGuard(sales_bp, home=inside)
    guard.runner = FakeRunner()
    results = guard.preflight()
    by_name = {r.name: r.status for r in results}
    assert by_name["home-isolation"] == "FAIL"
    detail = next(r.detail for r in results if r.name == "home-isolation")
    assert "inside the default hermes root" in detail


def test_deploy_dry_run_makes_no_calls(tmp_path, sales_bp):
    guard = DeployGuard(sales_bp, home=tmp_path)
    runner = FakeRunner()
    guard.runner = runner
    results = guard.deploy(dry_run=True)
    assert runner.calls == []
    assert any(r.name == "dry-run" for r in results)


def test_deploy_full_creates_profiles_crons_and_snapshot(tmp_path, sales_bp):
    home = tmp_path
    guard = DeployGuard(sales_bp, home=home)
    guard.runner = FakeRunner()
    results = guard.deploy()
    statuses = {r.name: r.status for r in results}
    assert all(r.status in ("PASS", "WARN") for r in results), results
    # every role profile materialized by the fake runner + snapshot written
    assert (home / "teams" / "sales" / ".teamops-sales.snapshot.json").exists()
    snap = json.loads((home / "teams" / "sales" / ".teamops-sales.snapshot.json").read_text())
    assert len(snap["profiles"]) == len(sales_bp.roles)


def test_smoke_reads_materialized_team(tmp_path, sales_bp):
    home = tmp_path
    guard = DeployGuard(sales_bp, home=home)
    guard.runner = FakeRunner()
    guard.deploy()
    smoke_guard = DeployGuard(sales_bp, home=home)
    smoke_guard.runner = FakeRunner()
    results = smoke_guard.smoke()
    statuses = {r.name: r.status for r in results}
    # profiles + souls green
    assert statuses["profile:teamops-sales-boss"] == "PASS"
    assert statuses["soul:teamops-sales-boss"] == "PASS"
    # shared kanban + mesh copied by deploy, parseable
    assert statuses["shared:kanban.json"] == "PASS"
    assert statuses["shared:mesh.json"] == "PASS"
    # crons materialized
    assert statuses["cron-present:sales-daily-digest"] == "PASS"
    assert all(r.status != "FAIL" for r in results)


def test_rollback_without_snapshot_is_skip(tmp_path, sales_bp):
    guard = DeployGuard(sales_bp, home=tmp_path)
    guard.runner = FakeRunner()
    results = guard.rollback()
    assert results[0].status == "SKIP"


def test_rollback_removes_profiles_and_snapshot(tmp_path, sales_bp):
    home = tmp_path
    guard = DeployGuard(sales_bp, home=home)
    guard.runner = FakeRunner()
    guard.deploy()
    rb = DeployGuard(sales_bp, home=home)
    rb.runner = FakeRunner()
    results = rb.rollback()
    by_name = {r.name: r.status for r in results}
    assert by_name["rollback-snapshot"] == "PASS"
    assert not (home / "teams" / "sales" / ".teamops-sales.snapshot.json").exists()


def test_rollback_removes_crons_by_id(tmp_path, sales_bp):
    home = tmp_path
    guard = DeployGuard(sales_bp, home=home)
    guard.runner = FakeRunner()
    guard.deploy()
    jobs_file = home / "profiles" / "teamops-sales-boss" / "cron" / "jobs.json"
    assert "sales-daily-digest" in jobs_file.read_text()
    rb = DeployGuard(sales_bp, home=home)
    rb.runner = FakeRunner()
    results = rb.rollback()
    by_name = {r.name: r.status for r in results}
    assert by_name["rollback-cron:sales-daily-digest"] in ("PASS", "SKIP")
    assert all(r.status != "FAIL" for r in results)


def test_blueprint_loader_property_mgmt():
    bp = Blueprint(ROOT / "teamops" / "blueprints" / "property-mgmt")
    assert bp.name == "property-mgmt"
    assert len(bp.roles) == 4
    names = [r["name"] for r in bp.roles]
    assert "tenant-line" in names and "maintenance-coordinator" in names


def test_fixture_billing_agent_loads():
    bp = Blueprint(ROOT / "teamops" / "blueprints" / "fixtures" / "billing-agent")
    assert bp.name == "billing-agent"
    assert len(bp.roles) == 7
