"""Model Guard: detect dead/free-gone/rate-limited models across the fleet;
recommend fallbacks. v1 = detect + recommend only — NEVER auto-swap."""
from __future__ import annotations

import json
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


def _model_error_markers(error: str) -> bool:
    return any(mk in (error or "").lower() for mk in MODEL_404_MARKERS)


def scan(home: Path) -> dict:
    """Scan all scopes for jobs whose pinned model is dead (404/free-gone).

    Two categories:
    - ``pinned``: job explicitly pins ``model`` and its errors show a dead-model
      marker → recommend a fallback pin.
    - ``default``: job has NO pinned model but errors a dead-model marker →
      the scope's default model is the problem; recommend re-pinning the job or
      the profile default (detect + recommend only, never auto-swap).
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
                "scope": scope, "job": name, "model": model, "provider": provider,
                "pinned": True, "error": err[:200], "recommend": rec,
            })
        else:
            dead.append({
                "scope": scope, "job": name, "model": "(profile default)", "provider": provider,
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
        "recommendation_note": "Detect + recommend only in v1 — no auto-swap. Pick a fallback and pin via `hermes cron edit <id> --model <m>`.",
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
