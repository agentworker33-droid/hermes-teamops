"""Build module: blueprint loader + deploy/verify/rollback orchestration.

Catalog review #5: blueprint names/paths resolve ONLY under the plugin's
``blueprints/`` dir or an owner-configured root — arbitrary filesystem paths
are rejected. Soul/shared rel paths are confined to the blueprint root by
``guard._require_inside`` at preflight/deploy time.
"""
from __future__ import annotations

from pathlib import Path

from .guard import DeployGuard, _require_inside

DEFAULT_BLUEPRINTS = Path(__file__).resolve().parent / "blueprints"


def _resolve_blueprint_root(blueprints_root: Path | None) -> Path:
    """Owner-configurable blueprint root: explicit arg, env var, or default."""
    if blueprints_root is not None:
        return Path(blueprints_root)
    import os
    env = os.environ.get("TEAMOPS_BLUEPRINTS_ROOT")
    if env:
        return Path(env).expanduser()
    return DEFAULT_BLUEPRINTS


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
            raise RuntimeError("PyYAML required (declare python_dependencies)") from e
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
        return _require_inside(self.root, Path(rel))


def load_blueprint(name_or_path: str, blueprints_root: Path | None = None) -> Blueprint:
    """Resolve a blueprint NAME under the allowed root (never an arbitrary path).

    ``name_or_path`` may be a bare name ('sales') or a path; if a path, it must
    resolve inside the allowed blueprint root, otherwise ValueError.
    """
    root_dir = _resolve_blueprint_root(blueprints_root).resolve()
    p = Path(name_or_path).expanduser()
    if p.is_absolute() or (p.parent != Path(".") and (p / "team.yaml").exists()):
        candidate = p if p.is_absolute() else (root_dir / p)
        cand_r = candidate.resolve()
        if cand_r != root_dir and root_dir not in cand_r.parents:
            raise ValueError(f"blueprint path escapes allowed root {root_dir}: {name_or_path}")
        root = cand_r if (cand_r / "team.yaml").exists() else cand_r.parent
    else:
        root = (root_dir / name_or_path).resolve()
        if root != root_dir and root_dir not in root.parents:
            raise ValueError(f"blueprint path escapes allowed root {root_dir}: {name_or_path}")
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


def verify_team(name_or_path: str, home: Path | None = None, chat_probe: bool = False,
                blueprints_root: Path | None = None) -> dict:
    bp = load_blueprint(name_or_path, blueprints_root)
    guard = DeployGuard(bp, home=home)
    guard.smoke(chat_probe=chat_probe)
    return guard.summary()


def rollback_team(name_or_path: str, home: Path | None = None, blueprints_root: Path | None = None) -> dict:
    try:
        bp = load_blueprint(name_or_path, blueprints_root)
        guard = DeployGuard(bp, home=home)
    except (FileNotFoundError, ValueError):
        # snapshot-driven: blueprint may live under fixtures/ or be gone — guard
        # without a blueprint, team name from the argument.
        guard = DeployGuard(home=home, team=Path(name_or_path).stem if Path(name_or_path).exists() else name_or_path)
    guard.rollback()
    return guard.summary()
