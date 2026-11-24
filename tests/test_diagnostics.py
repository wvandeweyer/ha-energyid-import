"""Tests for the diagnostics download."""

import json

from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.json import JSONEncoder
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from syrupy.assertion import SnapshotAssertion

from custom_components.energyid_import.diagnostics import (
    async_get_config_entry_diagnostics,
)

from .const import API_KEY, IMPORT_METER_ID, RECORD_ID, RECORD_NAME


@pytest.mark.usefixtures("mock_api")
async def test_diagnostics_redact_private_data(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    snapshot: SnapshotAssertion,
    freezer: FrozenDateTimeFactory,
) -> None:
    """The download has no key, record id, name, EAN or meter id."""
    freezer.move_to("2026-11-24 12:00:00+00:00")
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    entry = hass.config_entries.async_get_entry(mock_config_entry.entry_id)
    assert entry is not None
    result = await async_get_config_entry_diagnostics(hass, entry)

    # Home Assistant serves diagnostics as JSON; compare that form.
    text = json.dumps(result, cls=JSONEncoder)
    assert json.loads(text) == snapshot
    for secret in (API_KEY, RECORD_ID, RECORD_NAME, IMPORT_METER_ID, "0001"):
        assert secret not in text
