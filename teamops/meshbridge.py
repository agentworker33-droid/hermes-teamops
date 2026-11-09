"""Mesh sync: broadcast a decision to team MESH-MAP.md + shared Mnemosyne
surface with author attribution. Local-first: MESH-MAP.md under
<home>/teams/<team>/ is the durable record; shared Mnemosyne is best-effort
(plugin may not be installed in the target home)."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path


def _team_dirs(home: Path, team: str | None) -> list[Path]:
    teams_dir = home / "teams"
    if not teams_dir.is_dir():
        return []
    if team:
        d = teams_dir / team
        return [d] if d.is_dir() else []
    return sorted(d for d in teams_dir.iterdir() if d.is_dir())


def append_mesh_map(home: Path, message: str, author: str, team: str | None = None) -> list[dict]:
    """Append an author-attributed entry to each team's MESH-MAP.md."""
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    entries = []
    for td in _team_dirs(home, team):
        mm = td / "MESH-MAP.md"
        block = (f"\n## {now} — {author}\n"
                 f"{message.strip()}\n")
        if mm.exists():
            with open(mm, "a", encoding="utf-8") as fh:
                fh.write(block)
        else:
            td.mkdir(parents=True, exist_ok=True)
            header = (f"# {td.name} Team — Mesh Map (sync log)\n"
                      f"> Broadcasts are append-only and author-attributed.\n")
            with open(mm, "w", encoding="utf-8") as fh:
                fh.write(header + block)
        entries.append({"team": td.name, "file": str(mm), "author": author, "ts": now})
    return entries


def mnemosyne_shared(home: Path, message: str, author: str) -> dict:
    """Best-effort broadcast to the shared Mnemosyne surface with author metadata.

    Uses mnemosyne_shared_remember if the plugin is importable; otherwise records
    an outbox line under <home>/teams/.mesh-outbox.jsonl so it can be replayed.
    """
    entry = {"author": author, "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
             "message": message[:2000]}
    try:
        from mnemosyne import shared_surface  # type: ignore
        out = shared_surface.remember(
            content=message,
            metadata={"author": author, "source": "hermes-teamops"},
            scope="global" if hasattr(shared_surface, "remember") else "teamops",
        )
        return {"shared": True, "detail": str(out)[:200], "entry": entry}
    except Exception as e:  # noqa: BLE001
        outbox = home / "teams" / ".mesh-outbox.jsonl"
        try:
            outbox.parent.mkdir(parents=True, exist_ok=True)
            with open(outbox, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry) + "\n")
        except OSError:
            pass
        return {"shared": False, "reason": f"mnemosyne shared surface unavailable: {e}", "entry": entry}


def sync(home: Path, message: str, author: str = "teamops", team: str | None = None) -> dict:
    entries = append_mesh_map(home, message, author, team)
    shared = mnemosyne_shared(home, message, author)
    return {
        "success": True,
        "author": author,
        "message": message[:2000],
        "mesh_maps_updated": entries,
        "shared": shared,
    }
