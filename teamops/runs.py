"""Run evidence for the TeamOps workflow view (Task 2.13).

A "run" is one job execution with a node graph: blueprint deploys launched
from the dashboard, ephemeral one-shot teams (research-swarm, agentic-coding),
or canonical run records written to ``<home>/plugin-data/teamops-dashboard/
runs/*.json`` (the demo fixture writes those). The view is read-only: nothing
here retries, settles or cancels a run.

Sources are adapters over ONE shape (``run record``) so a new source is one
adapter, never a new view:
- canonical records (our own emitted JSON) — richest
- dashboard action logs (``plugin-data/teamops-dashboard/actions/*.log``)
- ephemeral team logs (``<home>/<stem>*.log`` / ``<home>/<stem>/*.log``)

Every source degrades honestly: a log with no markers becomes a single-node
run with state "unknown"/"done", never a fabricated graph.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

DONE_MARKERS = ("== DEMO COMPLETE ==", "DEMO COMPLETE", "ALL LANES COMPLETE",
                "ALL STAGES COMPLETE", "DONE --", "== DONE", "SUCCESS --")
FAIL_MARKERS = ("Traceback (most recent call last)", "FATAL:", "CRITICAL ERROR:")

_STEP_RE = re.compile(r"^\s*(?:step|##|###|==|--)\s*[0-9]*\s*[\"']?([A-Za-z0-9][^\"'\n]{0,60})", re.IGNORECASE)
_SKIP_MARKERS = ("== DEMO COMPLETE ==", "== DONE", "ALL LANES COMPLETE",
                 "ALL STAGES COMPLETE", "SUCCESS --", "== DEMO COMPLETE ==")


def _esc_label(text: str, cap: int = 72) -> str:
    t = re.sub(r"\s+", " ", str(text)).strip().strip('"').strip("'")
    return t[:cap] if t else "run"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _states_from_text(text: str) -> tuple[str, list[str]]:
    """(run_state, step labels) from a log body; honest heuristics."""
    steps: list[str] = []
    failed = False
    for line in text.splitlines():
        low = line.lower()
        if any(m.lower() in low for m in FAIL_MARKERS):
            failed = True
        if any(m.lower() in low for m in _SKIP_MARKERS):
            continue
        m = _STEP_RE.match(line)
        if m:
            label = _esc_label(m.group(1))
            if not steps or steps[-1] != label:
                steps.append(label)
    if failed:
        return "failed", steps
    if any(m in text for m in DONE_MARKERS):
        return "done", steps
    return "unknown", steps  # caller decides running vs done by age


def _parse_log(path: Path) -> dict | None:
    """One log file -> a run record (or None if unreadable)."""
    try:
        body = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None
    if not body.strip():
        return None
    state, steps = _states_from_text(body)
    age_s = (_utcnow().timestamp() - path.stat().st_mtime)
    if state == "unknown":
        state = "running" if age_s < 300 else "done"
    nodes = []
    if not steps:
        nodes = [{"id": "run", "label": _esc_label(path.stem.replace("-", " "), 48),
                  "state": state, "deps": [], "lane": None, "model": None,
                  "summary": None, "attempts": 1}]
    else:
        for i, label in enumerate(steps):
            nodes.append({"id": f"n{i}", "label": label, "state": state,
                          "deps": [f"n{i-1}"] if i else [], "lane": None,
                          "model": None,
                          "summary": (f"step {i+1}: {label}" if state == "running" else None),
                          "attempts": 1})
    nm = path.stat().st_mtime
    return {
        "id": f"log-{path.stem[:48]}",
        "kind": "log",
        "team": path.stem.split("-")[0],
        "source": str(path),
        "title": _esc_label(path.stem.replace("-", " "), 72),
        "state": state,
        "started": datetime.fromtimestamp(nm, timezone.utc).isoformat(),
        "ended": None if state == "running" else datetime.fromtimestamp(nm, timezone.utc).isoformat(),
        "nodes": nodes,
    }


def _canonical_runs(home: Path) -> list[dict]:
    d = home / "plugin-data" / "teamops-dashboard" / "runs"
    if not d.is_dir():
        return []
    out = []
    for f in sorted(d.glob("*.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        items = data if isinstance(data, list) else [data]
        for rec in items:
            if isinstance(rec, dict) and isinstance(rec.get("nodes"), list):
                rec.setdefault("source", str(f))
                out.append(rec)
    return out


def _action_log_runs(home: Path) -> list[dict]:
    d = home / "plugin-data" / "teamops-dashboard" / "actions"
    if not d.is_dir():
        return []
    out = []
    for f in sorted(d.glob("*.log")):
        stem = f.stem
        # names: <script>-<ts>.log or deploy-<name>-<ts>.log (see server.py)
        base = re.sub(r"-\d{8}-\d{6}$", "", stem)
        rec = _parse_log(f)
        if rec:
            rec["team"] = base
            rec["title"] = ("Deploy " + _esc_label(base.replace("deploy-", ""), 48)
                            if base.startswith("deploy-") else "Run " + _esc_label(base, 48))
            rec["kind"] = "deploy" if base.startswith("deploy-") else "script"
            out.append(rec)
    return out


def _ephemeral_log_runs(home: Path) -> list[dict]:
    """Newest log per ephemeral team (by script stem) as a run."""
    out = []
    try:
        from . import fleet
        stems = [Path(t.get("script", "")).stem for t in (fleet.ephemeral_teams(home) or [])]
    except Exception:  # noqa: BLE001
        stems = []
    for stem in stems:
        if not stem:
            continue
        cands: list[Path] = []
        for pattern in (f"{stem}*.log", f"{stem}/*.log", f"{stem}/runs/*.log"):
            cands += list(home.glob(pattern))
        if not cands:
            continue
        newest = max(cands, key=lambda p: p.stat().st_mtime)
        rec = _parse_log(newest)
        if rec:
            rec["team"] = stem
            rec["title"] = f"Team run · {stem.replace('-', ' ')}"
            rec["kind"] = "swarm"
            out.append(rec)
    return out


def recent_runs(home: Path, limit: int = 12) -> list[dict]:
    """All run records, newest first (by started time / file mtime)."""
    records = []
    try:
        records += _canonical_runs(home)
    except Exception:  # noqa: BLE001
        pass
    try:
        records += _action_log_runs(home)
    except Exception:  # noqa: BLE001
        pass
    try:
        records += _ephemeral_log_runs(home)
    except Exception:  # noqa: BLE001
        pass

    def _key(r: dict):
        try:
            return datetime.fromisoformat(str(r.get("started") or "").replace("Z", "+00:00")).timestamp()
        except ValueError:
            return 0.0
    records.sort(key=_key, reverse=True)
    return records[:limit]


def _layer_of(nodes: list[dict]) -> dict[str, int]:
    """Longest-path layer per node id (0 = first). deps on unknown ids ignored."""
    ids = {n.get("id") for n in nodes}
    memo: dict[str, int] = {}

    def visit(node_id: str) -> int:
        if node_id in memo:
            return memo[node_id]
        node = next((n for n in nodes if n.get("id") == node_id), None)
        if node is None:
            return 0
        best = 0
        for dep in node.get("deps") or []:
            if dep in ids:
                best = max(best, visit(dep) + 1)
        memo[node_id] = best
        return best

    for n in nodes:
        visit(str(n.get("id") or ""))
    return memo


def graph_of(rec: dict) -> dict:
    """Normalise one run record for the frontend graph."""
    nodes = rec.get("nodes") or []
    if not nodes:
        nodes = [{"id": "run", "label": "run", "state": rec.get("state", "done"),
                  "deps": [], "lane": None, "model": None, "summary": None, "attempts": 1}]
    layers = _layer_of(nodes)
    normalized = []
    for n in nodes:
        nid = str(n.get("id", ""))
        normalized.append({
            "id": nid,
            "label": _esc_label(n.get("label") or nid, 56),
            "state": n.get("state") or "unknown",
            "deps": [str(d) for d in (n.get("deps") or [])],
            "lane": n.get("lane"),
            "model": n.get("model"),
            "summary": n.get("summary"),
            "attempts": n.get("attempts") or 1,
            "layer": layers.get(nid, 0),
        })
    counts = {"nodes": len(normalized), "done": 0, "running": 0, "failed": 0, "unknown": 0}
    for n in normalized:
        st = n["state"]
        if st in counts:
            counts[st] += 1
        else:
            counts["unknown"] += 1
    return {
        "id": rec.get("id", "run"),
        "kind": rec.get("kind", "run"),
        "team": rec.get("team", ""),
        "title": rec.get("title") or "Run",
        "state": rec.get("state") or "unknown",
        "started": rec.get("started"),
        "source": rec.get("source", ""),
        "nodes": normalized,
        "counts": counts,
    }
