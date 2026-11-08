"""Blueprint loader tests (D0)."""
from pathlib import Path

import pytest

from teamops.build import Blueprint


@pytest.fixture(scope="module")
def sales_blueprint() -> Blueprint:
    root = Path(__file__).resolve().parent.parent / "teamops" / "blueprints" / "sales"
    return Blueprint(root)


def test_loads_team_name(sales_blueprint):
    assert sales_blueprint.name == "sales"


def test_loads_six_roles(sales_blueprint):
    assert len(sales_blueprint.roles) >= 6


def test_every_role_has_name_and_soul(sales_blueprint):
    for role in sales_blueprint.roles:
        assert role.get("name")
        assert role.get("soul"), f"role {role.get('name')} missing soul path"


def test_loads_crons(sales_blueprint):
    assert len(sales_blueprint.crons) >= 1
