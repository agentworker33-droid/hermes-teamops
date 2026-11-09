"""Model Guard: dead-model detection (pinned + profile-default), recommend-only."""
from teamops import modelguard

from conftest import FakeHome


def _job(jid: str, name: str, **kw) -> dict:
    base = {
        "id": jid, "name": name, "enabled": True, "model": "deepseek/deepseek-v4-flash-0731",
        "provider": "deepseek", "last_status": "ok", "last_error": None,
        "failure_streak": 0, "schedule": {"expr": "0 9 * * *"},
    }
    base.update(kw)
    return base


def test_healthy_when_no_dead_models(tmp_path):
    home = tmp_path / "home"
    FakeHome(home).add_job(_job("h1", "ok-job")).write()
    r = modelguard.scan(home)
    assert r["healthy"] is True
    assert r["dead"] == []


def test_flags_pinned_dead_model_and_recommends(tmp_path):
    home = tmp_path / "home"
    FakeHome(home).add_job(_job(
        "h1", "pinned-job", model="meituan/longcat-2.5-preview:free", provider="meituan",
        last_status="error",
        last_error="RuntimeError: HTTP 404: This model is no longer free. To continue using LongCat, switch to the paid 'meituan/longcat-2.0'.",
    )).write()
    r = modelguard.scan(home)
    assert r["healthy"] is False
    d = r["dead"][0]
    assert d["pinned"] is True
    assert d["recommend"]  # non-empty recommendation


def test_flags_profile_default_dead_model(tmp_path):
    home = tmp_path / "home"
    # no pinned model -> default-model case
    FakeHome(home).add_job(_job(
        "h1", "default-job", model=None, provider=None,
        last_status="error",
        last_error="RuntimeError: HTTP 404: This model is no longer free.",
    )).write()
    r = modelguard.scan(home)
    assert r["healthy"] is False
    d = r["dead"][0]
    assert d["pinned"] is False
    assert "(profile default)" in d["model"]


def test_ignores_unrelated_errors(tmp_path):
    home = tmp_path / "home"
    FakeHome(home).add_job(_job("h1", "cfg-job", last_status="error",
                                last_error="[blocked_config] provider credential missing")).write()
    r = modelguard.scan(home)
    assert r["healthy"] is True
