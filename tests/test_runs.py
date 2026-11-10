"""Run view (Task 2.13): log parsing, canonical records, graph layers."""
import json
import os
import time
from pathlib import Path

from teamops import runs


def _touch(path: Path, age_s: float = 0.0) -> None:
    now = time.time()
    os.utime(path, (now - age_s, now - age_s))


def test_parse_log_done_with_steps(tmp_path):
    p = tmp_path / "research-swarm-20261110-120000.log"
    p.write_text("### Preflight parse\n### Deploy lanes\n== DEMO COMPLETE ==\n")
    _touch(p, 600)
    rec = runs._parse_log(p)
    assert rec is not None
    assert rec["state"] == "done"
    assert [n["label"] for n in rec["nodes"]] == ["Preflight parse", "Deploy lanes"]
    assert rec["nodes"][1]["deps"] == ["n0"]


def test_parse_log_failed_on_traceback(tmp_path):
    p = tmp_path / "job.log"
    p.write_text("### Start\nTraceback (most recent call last):\n  File \"x.py\"\n")
    rec = runs._parse_log(p)
    assert rec is not None
    assert rec["state"] == "failed"


def test_parse_log_recent_is_running(tmp_path):
    p = tmp_path / "job.log"
    p.write_text("### First stage\n")
    _touch(p, 30)  # under 5 min -> running
    rec = runs._parse_log(p)
    assert rec is not None
    assert rec["state"] == "running"


def test_parse_log_old_unknown_is_done(tmp_path):
    p = tmp_path / "job.log"
    p.write_text("### First stage\n")
    _touch(p, 3600)  # older than 5 min -> done
    rec = runs._parse_log(p)
    assert rec is not None
    assert rec["state"] == "done"


def test_graph_of_layers_chain_and_branch():
    rec = {
        "id": "r1", "state": "done", "nodes": [
            {"id": "a", "label": "a", "state": "done", "deps": []},
            {"id": "b", "label": "b", "state": "done", "deps": ["a"]},
            {"id": "c", "label": "c", "state": "running", "deps": ["a"]},
            {"id": "d", "label": "d", "state": "waiting", "deps": ["b", "c"]},
        ],
    }
    g = runs.graph_of(rec)
    layers = {n["id"]: n["layer"] for n in g["nodes"]}
    assert layers["a"] == 0
    assert layers["b"] == 1 and layers["c"] == 1
    assert layers["d"] == 2
    assert g["counts"]["nodes"] == 4
    assert g["counts"]["running"] == 1


def test_recent_runs_canonical(tmp_path):
    home = tmp_path / "home"
    d = home / "plugin-data" / "teamops-dashboard" / "runs"
    d.mkdir(parents=True)
    older = {"id": "r-old", "state": "done", "title": "old", "started": "2026-01-01T00:00:00+00:00",
             "nodes": [{"id": "n", "label": "n", "state": "done", "deps": []}]}
    newer = {"id": "r-new", "state": "done", "title": "new", "started": "2026-11-01T00:00:00+00:00",
             "nodes": [{"id": "n", "label": "n", "state": "done", "deps": []}]}
    (d / "older.json").write_text(json.dumps(older))
    (d / "newer.json").write_text(json.dumps(newer))
    out = runs.recent_runs(home)
    assert [r["id"] for r in out] == ["r-new", "r-old"]


def test_recent_runs_empty(tmp_path):
    assert runs.recent_runs(tmp_path / "home") == []
