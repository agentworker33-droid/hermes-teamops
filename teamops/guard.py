"""Deploy Guard: preflight / smoke / rollback for LOCAL team deploys.

Ported concepts from ~/validate-onboard.sh + ~/onboard-self-serve/scripts/check-deploy-live.sh
but generalized to any HERMES_HOME (no VPS, no systemd assumptions).

D1 build shift implements: preflight(), deploy(), smoke(), rollback() and the
verify.report table.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .build import Blueprint


@dataclass
class CheckResult:
    name: str
    status: str  # PASS | FAIL | SKIP
    detail: str = ""
    extra: dict = field(default_factory=dict)


class DeployGuard:
    def __init__(self, blueprint: Blueprint, home=None, prefix: str = "teamops-"):
        self.blueprint = blueprint
        self.home = home
        self.prefix = prefix
        self.checks: list[CheckResult] = []

    def preflight(self) -> list[CheckResult]:
        raise NotImplementedError("D1")

    def deploy(self, dry_run: bool = False) -> list[CheckResult]:
        raise NotImplementedError("D1")

    def smoke(self) -> list[CheckResult]:
        raise NotImplementedError("D1")

    def rollback(self) -> list[CheckResult]:
        raise NotImplementedError("D1")
