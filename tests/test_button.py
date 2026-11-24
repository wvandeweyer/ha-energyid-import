"""Tests for the sync button."""

from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
)

from .conftest import DATA_URL_PATTERN, load_fixture
from .const import METERS_URL, RECORD_URL

BUTTON = "button.dummy_home_sync_now"


async def press(hass: HomeAssistant) -> None:
    """Press the sync button."""
    await hass.services.async_call(
        "button", "press", {ATTR_ENTITY_ID: BUTTON}, blocking=True
    )


@pytest.mark.usefixtures("mock_api")
async def test_button_syncs_the_record(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Pressing the button fetches the record again."""
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    before = aioclient_mock.call_count

    await press(hass)

    assert aioclient_mock.call_count > before
    state = hass.states.get(BUTTON)
    assert state is not None
    assert state.state != "unknown"


@pytest.mark.allow_warnings
async def test_button_reports_a_failed_sync(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    """A failing sync raises a translated error."""
    aioclient_mock.get(RECORD_URL, json=load_fixture("record.json"))
    aioclient_mock.get(METERS_URL, json=load_fixture("meters.json"))
    aioclient_mock.get(DATA_URL_PATTERN, json=load_fixture("data_pt1h_3d.json"))
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    aioclient_mock.clear_requests()
    aioclient_mock.get(RECORD_URL, status=404)

    with pytest.raises(HomeAssistantError) as info:
        await press(hass)

    assert info.value.translation_key == "sync_failed"
