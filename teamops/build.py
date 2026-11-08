"""Build module: blueprint loader + deploy/verify/rollback orchestration."""
from __future__ import annotations

from pathlib import Path

from .guard import DeployGuard


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


def load_blueprint(name_or_path: str, blueprints_root: Path | None = None) -> Blueprint:
    """Resolve a blueprint name (or path) to a Blueprint."""
    p = Path(name_or_path)
    if p.exists():
        root = p if p.name == "team.yaml" or (p / "team.yaml").exists() else p
        if not (root / "team.yaml").exists():
            # maybe passed path to team.yaml itself
            root = p.parent
        return Blueprint(root)
    root = (blueprints_root or Path(__file__).resolve().parent / "blueprints") / name_or_path
    return Blueprint(root)


def deploy_team(name_or_path: str, home: Path | None = None, prefix: str | None = None,
                dry_run: bool = False, chat_probe: bool = False, blueprints_root: Path | None = None) -> dict:
    bp = load_blueprint(name_or_path, blueprints_root)
    guard = DeployGuard(bp, home=home, prefix=prefix)
    guard.preflight()
    if not guard.summary()["green"] and not dry_run:
        return guard.summary()
    guard.deploy(dry_run=dry_run, chat_probe=chat_probe)
    return guard.summary()


def verify_team(name_or_path: str, home: Path | None = None, chat_probe: bool = False) -> dict:
    bp = load_blueprint(name_or_path)
    guard = DeployGuard(bp, home=home)
    guard.smoke(chat_probe=chat_probe)
    return guard.summary()


def rollback_team(name_or_path: str, home: Path | None = None) -> dict:
    bp = load_blueprint(name_or_path)
    guard = DeployGuard(bp, home=home)
    guard.rollback()
    return guard.summary()
