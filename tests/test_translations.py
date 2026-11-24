"""Tests that strings.json and the translations stay in sync."""

import json
from pathlib import Path

import pytest

PACKAGE = Path(__file__).parent.parent / "custom_components" / "energyid_import"


def flatten(value: object, prefix: str = "") -> set[str]:
    """Return every key path of a nested translation object."""
    if not isinstance(value, dict):
        return {prefix}
    keys: set[str] = set()
    for key, item in value.items():
        keys |= flatten(item, f"{prefix}.{key}" if prefix else str(key))
    return keys


def keys_of(path: Path) -> set[str]:
    """Return the key paths of one JSON file."""
    return flatten(json.loads(path.read_text(encoding="utf-8")))


@pytest.mark.parametrize("language", ["en", "nl"])
def test_translation_keys_match_strings(language: str) -> None:
    """Every key in strings.json exists in the translation and vice versa."""
    strings = keys_of(PACKAGE / "strings.json")
    translation = keys_of(PACKAGE / "translations" / f"{language}.json")

    assert strings == translation
