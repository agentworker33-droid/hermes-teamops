"""Task 2.9 — Model Guard OPT-IN auto-swap: pool logic, config, apply,
changelog, rollback. Graceful-disable without a pool is part of the contract.
"""
import json

from teamops import cli, modelguard, tools

from conftest import FakeHome, now_iso

DEAD = "meituan/longcat-2.5-preview:free"
LIVE = "deepseek/deepseek-v4-flash-0731"
ALT = "nous/nousresearch-hermes-3.5-flash"


def _job(jid: str, name: str, **kw) -> dict:
    base = {
        "id": jid, "name": name, "enabled": True, "model": LIVE, "provider": "deepseek",
        "last_status": "ok", "last_error": None, "failure_streak": 0,
        "schedule": {"expr": "0 9 * * *"},
    }
    base.update(kw)
    return base


def _home_with_dead_pinned(tmp_path) -> FakeHome:
    home = tmp_path / "home"
    return (FakeHome(home)
            .add_job(_job("h1", "dead-pinned", model=DEAD, provider="meituan",
                          last_status="error",
                          last_error="RuntimeError: HTTP 404: This model is no longer free.",
                          failure_streak=2))
            .add_job(_job("h2", "healthy", model=LIVE, provider="deepseek")))


# ---------------------------------------------------------------- pool logic

def test_pick_fallback_first_valid_different():
    assert modelguard._pick_fallback([DEAD, LIVE, ALT], DEAD) == LIVE
    assert modelguard._pick_fallback([LIVE], DEAD) == LIVE


def test_pick_fallback_returns_none_without_pool_or_all_same():
    assert modelguard._pick_fallback([], DEAD) is None
    assert modelguard._pick_fallback([DEAD], DEAD) is None


def test_valid_model_rejects_garbage():
    assert modelguard.valid_model("deepseek/deepseek-v4-flash-0731")
    assert modelguard.valid_model("nous/hermes-3.5-flash:free")
    assert not modelguard.valid_model("not a model")
    assert not modelguard.valid_model("")
    assert not modelguard.valid_model("no-slash")


# ------------------------------------------------------------------ config

def test_read_config_missing_degrades_empty(tmp_path):
    home = tmp_path / "home"
    home.mkdir(parents=True)
    cfg = modelguard.read_config(home)
    assert cfg["fallback_pool"] == []


def test_read_config_parses_pool(tmp_path):
    home = tmp_path / "home"
    p = modelguard.config_path(home)
    p.parent.mkdir(parents=True)
    p.write_text(f"modelguard:\n  fallback_pool:\n    - {LIVE}\n    - {ALT}\n")
    assert modelguard.read_config(home)["fallback_pool"] == [LIVE, ALT]


def test_read_config_bad_yaml_degrades_empty(tmp_path):
    home = tmp_path / "home"
    p = modelguard.config_path(home)
    p.parent.mkdir(parents=True)
    p.write_text("modelguard: [unclosed\n")
    assert modelguard.read_config(home)["fallback_pool"] == []


# -------------------------------------------------------------------- swap

def test_swap_dry_run_plans_and_writes_nothing(tmp_path):
    home = tmp_path / "home"
    fh = _home_with_dead_pinned(tmp_path).write()
    r = modelguard.swap(home, pool=[LIVE], dry_run=True)
    assert r["ok"] is True and r["mode"] == "plan"
    assert r["planned"]["job_swap_count"] == 1
    assert r["planned"]["job_swaps"][0]["from_model"] == DEAD
    assert r["planned"]["job_swaps"][0]["to_model"] == LIVE
    # nothing written
    doc = json.loads((home / "cron" / "jobs.json").read_text())
    assert doc["jobs"][0]["model"] == DEAD
    assert not (home / "plugin-data" / "teamops" / "model-swaps.json").exists()


def test_swap_apply_updates_jobs_json_and_changelog(tmp_path):
    home = tmp_path / "home"
    _home_with_dead_pinned(tmp_path).write()
    r = modelguard.swap(home, pool=[LIVE], dry_run=False)
    assert r["ok"] is True and r["mode"] == "apply"
    assert r["swapped"] == 1
    doc = json.loads((home / "cron" / "jobs.json").read_text())
    by_id = {j["id"]: j for j in doc["jobs"]}
    assert by_id["h1"]["model"] == LIVE   # dead pin swapped
    assert by_id["h2"]["model"] == LIVE   # healthy untouched (same model, no rewrite)
    changelog = json.loads((home / "plugin-data" / "teamops" / "model-swaps.json").read_text())
    assert len(changelog) == 1
    assert changelog[0]["kind"] == "job" and changelog[0]["job_id"] == "h1"
    assert changelog[0]["from_model"] == DEAD and changelog[0]["to_model"] == LIVE


def test_swap_scope_default_updates_config_yaml(tmp_path):
    home = tmp_path / "home"
    fh = FakeHome(home)
    # unpinned job + dead-model error => the scope default is the suspect
    fh.add_job(_job("h1", "default-dead", model=None, provider=None, last_status="error",
                    last_error="RuntimeError: HTTP 404: This model is no longer free."))
    fh.write()
    (home / "config.yaml").write_text(f"model:\n  default: {DEAD}\n  provider: meituan\n")
    r = modelguard.swap(home, pool=[LIVE, ALT], dry_run=False)
    assert r["ok"] is True
    assert r["default_swapped"] == 1
    import yaml
    doc = yaml.safe_load((home / "config.yaml").read_text())
    assert doc["model"]["default"] == LIVE
    cl = json.loads((home / "plugin-data" / "teamops" / "model-swaps.json").read_text())
    assert cl[0]["kind"] == "default" and cl[0]["from_model"] == DEAD


def test_swap_skips_scope_without_config_for_default_job(tmp_path):
    home = tmp_path / "home"
    fh = FakeHome(home)
    # profile-scope unpinned dead job; profile has NO config.yaml -> skipped, no write
    fh.add_job(_job("p1", "prof-default-dead", model=None, provider=None, last_status="error",
                    last_error="RuntimeError: HTTP 404: This model is no longer free."),
               scope="sales-dev")
    fh.write()
    r = modelguard.swap(home, pool=[LIVE], dry_run=False)
    assert r["ok"] is True
    assert r["job_swapped"] == 0 and r["default_swapped"] == 0
    assert any("no" in s["reason"] and "config" in s["reason"] for s in r["skipped"])
    assert not (home / "plugin-data" / "teamops" / "model-swaps.json").exists()


def test_swap_refuses_without_pool_and_writes_nothing(tmp_path):
    home = tmp_path / "home"
    _home_with_dead_pinned(tmp_path).write()
    r = modelguard.swap(home, pool=None, dry_run=False)
    assert r["ok"] is False and r["mode"] == "disabled"
    assert "fallback_pool" in r["error"]
    doc = json.loads((home / "cron" / "jobs.json").read_text())
    assert doc["jobs"][0]["model"] == DEAD  # untouched
    assert not (home / "plugin-data" / "teamops" / "model-swaps.json").exists()


def test_swap_uses_config_pool_when_explicit_pool_absent(tmp_path):
    home = tmp_path / "home"
    _home_with_dead_pinned(tmp_path).write()
    # pool comes from config/teamops.yaml, not the argument
    cp = modelguard.config_path(home)
    cp.parent.mkdir(parents=True)
    cp.write_text(f"modelguard:\n  fallback_pool:\n    - {LIVE}\n")
    r = modelguard.swap(home, pool=None, dry_run=False)
    assert r["ok"] is True and r["swapped"] == 1


def test_swap_filters_invalid_pool_entries(tmp_path):
    home = tmp_path / "home"
    _home_with_dead_pinned(tmp_path).write()
    r = modelguard.swap(home, pool=["garbage entry", LIVE], dry_run=True)
    assert r["invalid_pool"] == ["garbage entry"]
    assert r["pool"] == [LIVE]
    assert r["planned"]["job_swap_count"] == 1


# ---------------------------------------------------------------- rollback

def test_rollback_reverts_last_swap_and_marks_changelog(tmp_path):
    home = tmp_path / "home"
    _home_with_dead_pinned(tmp_path).write()
    modelguard.swap(home, pool=[LIVE], dry_run=False)
    r = modelguard.rollback(home)
    assert r["ok"] is True and r["rolled_back"] == 1
    doc = json.loads((home / "cron" / "jobs.json").read_text())
    assert next(j for j in doc["jobs"] if j["id"] == "h1")["model"] == DEAD
    changelog = json.loads((home / "plugin-data" / "teamops" / "model-swaps.json").read_text())
    assert changelog[0]["reverted"] is True
    # second rollback: nothing pending
    r2 = modelguard.rollback(home)
    assert r2["rolled_back"] == 0


def test_rollback_all_reverts_job_and_default(tmp_path):
    home = tmp_path / "home"
    fh = FakeHome(home)
    fh.add_job(_job("h1", "dead-pinned", model=DEAD, provider="meituan", last_status="error",
                    last_error="RuntimeError: HTTP 404: This model is no longer free."))
    fh.add_job(_job("h2", "default-dead", model=None, provider=None, last_status="error",
                    last_error="RuntimeError: HTTP 404: This model is no longer free."))
    fh.write()
    (home / "config.yaml").write_text(f"model:\n  default: {DEAD}\n")
    modelguard.swap(home, pool=[LIVE], dry_run=False)
    assert modelguard.rollback(home, all_swaps=True)["rolled_back"] == 2
    doc = json.loads((home / "cron" / "jobs.json").read_text())
    assert next(j for j in doc["jobs"] if j["id"] == "h1")["model"] == DEAD
    import yaml
    assert yaml.safe_load((home / "config.yaml").read_text())["model"]["default"] == DEAD


def test_rollback_skips_drift_without_clobber(tmp_path):
    home = tmp_path / "home"
    _home_with_dead_pinned(tmp_path).write()
    modelguard.swap(home, pool=[LIVE], dry_run=False)
    # someone else repins the job after the swap -> rollback must not clobber
    doc = json.loads((home / "cron" / "jobs.json").read_text())
    doc["jobs"][0]["model"] = ALT
    (home / "cron" / "jobs.json").write_text(json.dumps(doc, indent=2))
    r = modelguard.rollback(home)
    assert r["rolled_back"] == 0
    assert any("drifted" in d["reason"] for d in r["drift_skipped"])
    assert next(j for j in doc["jobs"] if j["id"] == "h1")["model"] == ALT


def test_rollback_with_no_changelog_is_benign(tmp_path):
    home = tmp_path / "home"
    home.mkdir(parents=True)
    r = modelguard.rollback(home)
    assert r["ok"] is True and r["rolled_back"] == 0


# ------------------------------------------------------------ tool + CLI

def test_tool_model_scan_swap_dry_run_and_apply(tmp_path):
    home = tmp_path / "home"
    _home_with_dead_pinned(tmp_path).write()
    plan = json.loads(tools.model_scan(home=str(home), swap=True, pool=[LIVE]))
    assert plan["success"] is True
    assert plan["dry_run"] is True and plan["planned"]["job_swap_count"] == 1
    applied = json.loads(tools.model_scan(home=str(home), swap=True, pool=[LIVE], dry_run=False))
    assert applied["success"] is True and applied["swapped"] == 1
    assert "changelog_path" in applied


def test_tool_model_scan_swap_without_pool_refuses(tmp_path):
    home = tmp_path / "home"
    _home_with_dead_pinned(tmp_path).write()
    out = json.loads(tools.model_scan(home=str(home), swap=True, dry_run=False))
    assert out["success"] is False
    assert "fallback_pool" in out["error"]


def test_cli_model_swap_apply_then_rollback(capsys, tmp_path):
    home = tmp_path / "home"
    _home_with_dead_pinned(tmp_path).write()
    rc = cli.main(["teams", "model-scan", "--swap", "--pool", f"{ALT},{LIVE}",
                   "--apply", "--home", str(home)])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["success"] is True and payload["swapped"] == 1
    doc = json.loads((home / "cron" / "jobs.json").read_text())
    assert next(j for j in doc["jobs"] if j["id"] == "h1")["model"] == ALT  # first pool entry
    rc2 = cli.main(["teams", "model-rollback", "--home", str(home)])
    assert rc2 == 0
    payload2 = json.loads(capsys.readouterr().out)
    assert payload2["success"] is True and payload2["rolled_back"] == 1
    doc2 = json.loads((home / "cron" / "jobs.json").read_text())
    assert next(j for j in doc2["jobs"] if j["id"] == "h1")["model"] == DEAD
