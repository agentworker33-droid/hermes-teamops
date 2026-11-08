"""Build module: blueprint loader + deploy/verify/rollback orchestration.

D0: loader implemented. Deploy/verify/rollback are implemented in `guard.py`
and wired here across the D1 build shift.
"""
from __future__ import annotations

from pathlib import Path


class Blueprint:
    def __init__(self, root: Path):
        self.root = root
        self.team = {}
        self.roles: list[dict] = []
        self.crons: list[dict] = []
        self._load()

    def _load(self) -> None:
        try:
            import yaml
        except ImportError as e:  # pragma: no cover
            raise RuntimeError("PyYAML required") from e
        team_path = self.root / "team.yaml"
        crons_path = self.root / "crons.yaml"
        if not team_path.exists():
            raise FileNotFoundError(f"blueprint missing team.yaml: {self.root}")
        self.team = yaml.safe_load(team_path.read_text()) or {}
        self.roles = list(self.team.get("roles") or [])
        if crons_path.exists():
            self.crons = list((yaml.safe_load(crons_path.read_text()) or {}).get("crons") or [])

    @property
    def name(self) -> str:
        return self.team.get("name") or self.root.name

    def soul_path(self, role: dict) -> Path | None:
        rel = role.get("soul")
        if not rel:
            return None
        return self.root / rel
