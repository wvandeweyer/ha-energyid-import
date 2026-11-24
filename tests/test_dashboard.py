"""Tests for the generate_dashboard action."""

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from syrupy.assertion import SnapshotAssertion

from custom_components.energyid_import.const import DOMAIN


async def generate(hass: HomeAssistant, entry: MockConfigEntry) -> object:
    """Call the action and return its response."""
    return await hass.services.async_call(
        DOMAIN,
        "generate_dashboard",
        {"config_entry_id": entry.entry_id},
        blocking=True,
        return_response=True,
    )


async def setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """Set up the entry."""
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


@pytest.mark.usefixtures("mock_api")
async def test_dashboard_lists_statistics_and_entities(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, snapshot: SnapshotAssertion
) -> None:
    """The dashboard has graphs and totals per group and the record entities."""
    await setup(hass, mock_config_entry)

    assert await generate(hass, mock_config_entry) == snapshot


@pytest.mark.usefixtures("mock_api")
async def test_dashboard_skips_removed_entities(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """An entity the user deleted is left out instead of breaking the card."""
    await setup(hass, mock_config_entry)
    er.async_get(hass).async_remove("button.dummy_home_sync_now")

    response = await generate(hass, mock_config_entry)

    assert "button.dummy_home_sync_now" not in str(response)
    assert "sensor.dummy_home_last_sync" in str(response)
