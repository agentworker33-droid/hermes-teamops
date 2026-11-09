"""Model Guard: detect dead/free-gone models across the fleet; recommend
fallbacks. v1 = detect + recommend only. Task 2.9 adds OPT-IN ``swap`` mode:
with a user-configured fallback pool, dead pinned models (and the scope's
``model.default`` when an unpinned job dies) are swapped to the first pool
fallback. The pool is the ONLY source of truth — the plugin never invents
models, and swap never runs without the pool set (graceful disable).
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from . import fleet

MODEL_404_MARKERS = ("404", "no longer free", "free period", "requires available", "model not found")

# Known-good fallback candidates, cheapest first (heuristic; user picks).
FALLBACK_POOL = [
    "deepseek/deepseek-v4-flash-0731",
    "nous/nousresearch-hermes-3.5-flash",
    "anthropic/claude-3.5-haiku",
]

# provider/model — provider may be namespaced (e.g. deepseek/, nous/), model may
# carry a :variant suffix. Spaces are never acceptable.
VALID_MODEL_RE = re.compile(r"^[A-Za-z0-9_.\-]+/[A-Za-z0-9_.\-:/]+$")


def _model_error_markers(error: str) -> bool:
    return any(mk in (error or "").lower() for mk in MODEL_404_MARKERS)


def scan(home: Path) -> dict:
    """Scan all scopes for jobs whose pinned model is dead (404/free-gone).

    Two categories:
    - ``pinned``: job explicitly pins ``model`` and its errors show a dead-model
      marker → recommend a fallback pin.
    - ``default``: job has NO pinned model but errors a dead-model marker →
      the scope's default model is the problem; recommend re-pinning the job or
      the profile default (detect + recommend only in scan; swap() is the
      opt-in executor).
    """
    now = datetime.now(timezone.utc)
    jobs = fleet.all_jobs(home)
    dead: list[dict] = []
    for scope, j in jobs:
        model = j.get("model") or ""
        provider = j.get("provider") or ""
        lerr = j.get("last_error") or ""
        dlerr = j.get("last_delivery_error") or ""
        name = j.get("name", j.get("id", ""))
        err = lerr or dlerr
        if not err or not _model_error_markers(err):
            continue
        if model:
            rec = _recommend(model)
            dead.append({
                "scope": scope, "job": name, "job_id": j.get("id", ""),
                "model": model, "provider": provider,
                "pinned": True, "error": err[:200], "recommend": rec,
            })
        else:
            dead.append({
                "scope": scope, "job": name, "job_id": j.get("id", ""),
                "model": "(profile default)", "provider": provider,
                "pinned": False, "error": err[:200],
                "recommend": f"pin this job to a live model, e.g. {FALLBACK_POOL[0]}",
            })
    # gather all distinct models in jobs for the report
    used = sorted({f"{p}/{m}".strip("/") for p, m in ((j.get("provider"), j.get("model")) for _, j in jobs) if m})
    return {
        "checked_at": now.isoformat(),
        "home": str(home),
        "models_in_use": used,
        "dead": dead,
        "dead_count": len(dead),
        "recommendation_note": "Detect + recommend only by default. OPT-IN auto-swap: "
                               "`model_scan(swap=True, pool=[...])` / "
                               "`hermes teams model-scan --swap --pool a,b --apply`.",
        "healthy": len(dead) == 0,
    }


def _recommend(model: str) -> str:
    """Cheap fallback suggestion; exact match preferred over heuristic."""
    for cand in FALLBACK_POOL:
        if "flash" in model.lower() and "flash" in cand and "deepseek" in cand:
            return cand
        if "deepseek" in model.lower() and "deepseek" in cand:
            return cand
    return FALLBACK_POOL[0]


# ---------------------------------------------------------------------------
# Task 2.9 — OPT-IN swap machinery
# ---------------------------------------------------------------------------

def valid_model(model: str) -> bool:
    return bool(VALID_MODEL_RE.match(model or ""))


def _pick_fallback(pool: list[str], current: str) -> str | None:
    """First *valid* pool entry that differs from the dead model. Never
    invents a model: anything not in the user's pool is unswappable."""
    for cand in pool:
        if not valid_model(cand):
            continue
        if cand == current:
            continue
        return cand
    return None


def config_path(home: Path) -> Path:
    return home / "config" / "teamops.yaml"


def read_config(home: Path) -> dict:
    """Read the plugin config: modelguard.fallback_pool. Missing/invalid
    config degrades to an empty pool (swap disabled, no nag)."""
    path = config_path(home)
    if not path.exists():
        return {"path": str(path), "fallback_pool": []}
    try:
        import yaml  # lazily; yaml is present in Hermes envs
        doc = yaml.safe_load(path.read_text()) or {}
        pool = (doc.get("modelguard") or {}).get("fallback_pool") or []
        if not isinstance(pool, list):
            pool = []
        return {"path": str(path), "fallback_pool": [str(x) for x in pool]}
    except Exception:  # noqa: BLE001 — bad config must never break the plugin
        return {"path": str(path), "fallback_pool": []}


def _changelog_path(home: Path) -> Path:
    return home / "plugin-data" / "teamops" / "model-swaps.json"


def _read_changelog(home: Path) -> list[dict]:
    p = _changelog_path(home)
    if not p.exists():
        return []
    try:
        return list(json.loads(p.read_text()))
    except (json.JSONDecodeError, ValueError):
        return []


def _write_changelog(home: Path, entries: list[dict]) -> Path:
    p = _changelog_path(home)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(entries, indent=2, ensure_ascii=False))
    return p


def _jobs_file(home: Path, scope: str) -> Path:
    if scope == "home":
        return fleet.jobs_path(home)
    return fleet.profile_jobs_path(home / "profiles" / scope)


def _config_file(home: Path, scope: str) -> Path:
    if scope == "home":
        return home / "config.yaml"
    return home / "profiles" / scope / "config.yaml"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def swap(home: Path, pool: list[str] | None = None, dry_run: bool = True) -> dict:
    """OPT-IN auto-swap of dead models to the first pool fallback.

    - ``pool``: explicit pool; when None the plugin reads
      ``<home>/config/teamops.yaml`` → ``modelguard.fallback_pool``.
    - Without ANY valid pool entry this refuses (feature disabled by design;
      zero writes, zero nags).
    - Dead pinned job → swap that job's ``model`` in its jobs.json.
    - Dead unpinned job → swap the scope's ``model.default`` in its
      config.yaml (home or profile config); profiles without a config file
      are skipped (never invented).
    - ``dry_run=True`` (default): plan only, no writes.
    - Every applied swap is recorded in a changelog; ``rollback()`` undoes it.
    """
    cfg = read_config(home)
    use_pool = list(pool) if pool is not None else list(cfg.get("fallback_pool", []))
    valid = [m for m in use_pool if valid_model(m)]
    invalid = [m for m in use_pool if not valid_model(m)]
    if not valid:
        return {
            "ok": False,
            "error": "swap disabled — no fallback_pool configured. Set "
                     f"modelguard.fallback_pool in {config_path(home)} or pass --pool "
                     "('provider/model' entries).",
            "mode": "disabled",
        }

    report = scan(home)
    job_swaps: list[dict] = []
    default_swaps: list[dict] = []
    skipped: list[dict] = []
    for d in report["dead"]:
        if d.get("pinned"):
            fb = _pick_fallback(valid, d.get("model", ""))
            if fb:
                job_swaps.append({
                    "scope": d["scope"], "job_id": d.get("job_id", ""),
                    "job": d.get("job", ""), "from_model": d.get("model", ""),
                    "to_model": fb, "provider": d.get("provider", ""),
                })
            else:
                skipped.append({"scope": d["scope"], "job": d.get("job", ""),
                                "reason": "no usable pool fallback for this model"})
        else:
            cpath = _config_file(home, d["scope"])
            if not cpath.exists():
                skipped.append({"scope": d["scope"], "job": d.get("job", ""),
                                "reason": f"no {cpath.name} at scope — job has no pinned model"
                                          " and no scope default to swap"})
                continue
            try:
                import yaml  # lazily
                doc = yaml.safe_load(cpath.read_text()) or {}
                cur = (doc.get("model") or {}).get("default", "")
            except Exception:  # noqa: BLE001
                skipped.append({"scope": d["scope"], "job": d.get("job", ""),
                                "reason": f"unreadable {cpath}"})
                continue
            if not cur:
                skipped.append({"scope": d["scope"], "job": d.get("job", ""),
                                "reason": "scope default model is empty"})
                continue
            fb = _pick_fallback(valid, cur)
            if fb:
                default_swaps.append({
                    "scope": d["scope"], "job": d.get("job", ""),
                    "path": str(cpath), "from_model": cur, "to_model": fb, "kind": "default",
                })
            else:
                skipped.append({"scope": d["scope"], "job": d.get("job", ""),
                                "reason": "no usable pool fallback for scope default"})

    planned = {
        "job_swaps": job_swaps,
        "default_swaps": default_swaps,
        "skipped": skipped,
        "job_swap_count": len(job_swaps),
        "default_swap_count": len(default_swaps),
        "dead_total": len(report["dead"]),
    }
    if dry_run:
        return {"ok": True, "mode": "plan", "dry_run": True, "pool": valid,
                "invalid_pool": invalid, "planned": planned}

    # ---- apply ----
    swaps_applied: list[dict] = []
    ts = _now_iso()
    by_file: dict[str, tuple[str, list[dict]]] = {}
    for s in job_swaps:
        scope = s["scope"]
        path = _jobs_file(home, scope)
        by_file.setdefault(str(path), (scope, []))[1].append(s)

    for pstr, (scope, swaps) in by_file.items():
        path = Path(pstr)
        if not path.exists():
            for s in swaps:
                skipped.append({"scope": scope, "job": s["job"],
                                "reason": f"jobs.json missing: {path}"})
            continue
        try:
            doc = json.loads(path.read_text())
        except (json.JSONDecodeError, ValueError):
            for s in swaps:
                skipped.append({"scope": scope, "job": s["job"],
                                "reason": f"unreadable jobs.json: {path}"})
            continue
        jobs = list(doc.get("jobs") or [])
        changed = False
        for s in swaps:
            for j in jobs:
                if j.get("id") == s["job_id"]:
                    if j.get("model") == s["from_model"]:
                        j["model"] = s["to_model"]
                        changed = True
                    swaps_applied.append({**s, "ts": ts, "kind": "job"})
                    break
        if changed:
            doc["updated_at"] = ts
            path.write_text(json.dumps(doc, indent=2, ensure_ascii=False))

    for s in default_swaps:
        cpath = Path(s["path"])
        try:
            import yaml  # lazily
            doc = yaml.safe_load(cpath.read_text()) or {}
        except Exception:  # noqa: BLE001
            skipped.append({"scope": s["scope"], "job": s["job"],
                            "reason": f"unreadable {cpath}"})
            continue
        if (doc.get("model") or {}).get("default") == s["from_model"]:
            doc.setdefault("model", {})["default"] = s["to_model"]
            cpath.write_text(yaml.safe_dump(doc, sort_keys=False, default_flow_style=False))
        swaps_applied.append({**s, "ts": ts})

    changelog = _read_changelog(home)
    changelog.extend(swaps_applied)
    if swaps_applied:
        changelog_path = str(_write_changelog(home, changelog))
    else:
        changelog_path = str(_changelog_path(home))  # not created — nothing happened
    return {
        "ok": True, "mode": "apply", "dry_run": False, "pool": valid,
        "invalid_pool": invalid,
        "swapped": len(swaps_applied),
        "job_swapped": len(job_swaps), "default_swapped": len(default_swaps),
        "skipped": skipped,
        "changelog_path": changelog_path,
    }


def rollback(home: Path, all_swaps: bool = False) -> dict:
    """Undo model-guard swaps recorded in the changelog.

    Default: the most recent un-reverted swap. ``all_swaps=True`` reverts every
    un-reverted swap in reverse order. Only reverts when the current value
    still matches what the swap wrote (drift is skipped, never clobbered).
    """
    changelog = _read_changelog(home)
    pending = [e for e in changelog if not e.get("reverted")]
    if not pending:
        return {"ok": True, "rolled_back": 0, "reverted": [], "pending": 0,
                "message": "no model-guard swaps to roll back (changelog empty or all reverted)"}
    targets = list(reversed(pending)) if all_swaps else pending[-1:]
    reverted: list[dict] = []
    drift: list[dict] = []
    ts = _now_iso()

    for e in targets:
        try:
            if e.get("kind") == "job":
                path = _jobs_file(home, e["scope"])
                doc = json.loads(path.read_text())
                jobs = list(doc.get("jobs") or [])
                hit = None
                for j in jobs:
                    if j.get("id") == e.get("job_id"):
                        hit = j
                        break
                if hit is None:
                    drift.append({"id": e.get("job_id"), "scope": e["scope"],
                                  "reason": "job no longer in jobs.json"})
                    continue
                if hit.get("model") != e.get("to_model"):
                    drift.append({"id": e.get("job_id"), "scope": e["scope"],
                                  "reason": f"model drifted to '{hit.get('model')}' — not reverting"})
                    continue
                hit["model"] = e.get("from_model")
                doc["updated_at"] = ts
                path.write_text(json.dumps(doc, indent=2, ensure_ascii=False))
            elif e.get("kind") == "default":
                path = Path(e["path"]) if e.get("path") else _config_file(home, e["scope"])
                import yaml  # lazily
                doc = yaml.safe_load(path.read_text()) or {}
                if (doc.get("model") or {}).get("default") != e.get("to_model"):
                    drift.append({"scope": e["scope"], "reason": "model.default drifted — not reverting"})
                    continue
                doc.setdefault("model", {})["default"] = e.get("from_model")
                path.write_text(yaml.safe_dump(doc, sort_keys=False, default_flow_style=False))
            else:  # legacy/untyped record
                drift.append({"id": e.get("job_id"), "scope": e.get("scope"),
                              "reason": f"unknown swap kind {e.get('kind')!r}"})
                continue
            e["reverted"] = True
            e["reverted_at"] = ts
            reverted.append(e)
        except Exception as exc:  # noqa: BLE001
            drift.append({"id": e.get("job_id"), "scope": e.get("scope"),
                          "reason": f"revert failed: {exc}"})

    _write_changelog(home, changelog)
    return {"ok": True, "rolled_back": len(reverted), "reverted": reverted,
            "drift_skipped": drift, "pending": sum(1 for e in changelog if not e.get("reverted"))}
