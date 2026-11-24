"""Tests that fixtures hold dummy identifiers and names only."""

import json
from pathlib import Path
import re

import pytest

FIXTURES = Path(__file__).parent / "fixtures"
DUMMY_ID = re.compile(r"^dummy-[a-z]+-\d+$")
ID_KEYS = frozenset({"id", "recordid", "meterid", "ownerid", "memberid", "userid"})
NAME_KEYS = frozenset({"displayname", "name", "fullname"})


def fields(value: object, key: str = "") -> list[tuple[str, object]]:
    """Return every (key, scalar value) pair in a JSON document."""
    if isinstance(value, list):
        return [pair for item in value for pair in fields(item, key)]
    if isinstance(value, dict):
        return [pair for k, v in value.items() for pair in fields(v, str(k))]
    return [(key.lower(), value)]


@pytest.mark.parametrize("path", sorted(FIXTURES.glob("*.json")), ids=lambda p: p.name)
def test_fixture_values_are_dummies(path: Path) -> None:
    """Ids match dummy-<kind>-<n> and names start with Dummy."""
    for key, value in fields(json.loads(path.read_text(encoding="utf-8"))):
        if key in ID_KEYS:
            assert isinstance(value, str), f"{path.name}: {key}"
            assert DUMMY_ID.match(value), f"{path.name}: {key}"
        if key in NAME_KEYS:
            assert isinstance(value, str), f"{path.name}: {key}"
            assert value.startswith("Dummy "), f"{path.name}: {key}"
