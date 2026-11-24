"""Tests for setting up and unloading EnergyID Import."""

from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntryState
from homeassistant.core import HomeAssistant
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
)

from custom_components.energyid_import.coordinator import (
    EnergyIDCoordinator,
    EnergyIDData,
)

from .conftest import DATA_URL_PATTERN, load_fixture
from .const import (
    EXPORT_METER_ID,
    IMPORT_METER_ID,
    METERS_URL,
    RECORD_URL,
    RECORDS_URL,
)


def state_of(entry: MockConfigEntry) -> ConfigEntryState:
    """Return the current state without letting mypy narrow it."""
    return entry.state


async def setup_entry(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """Add an entry and set it up."""
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


@pytest.mark.usefixtures("mock_api")
async def test_setup_unload_and_reload(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """The entry loads, unloads and reloads without a restart."""
    await setup_entry(hass, mock_config_entry)

    assert state_of(mock_config_entry) is ConfigEntryState.LOADED
    coordinator = mock_config_entry.runtime_data
    assert isinstance(coordinator, EnergyIDCoordinator)
    assert isinstance(coordinator.data, EnergyIDData)
    assert coordinator.client is not None
    assert [meter.id for meter in coordinator.data.meters] == [
        IMPORT_METER_ID,
        EXPORT_METER_ID,
    ]
    assert coordinator.data.missing_meter_ids == frozenset()

    assert await hass.config_entries.async_unload(mock_config_entry.entry_id)
    assert state_of(mock_config_entry) is ConfigEntryState.NOT_LOADED

    assert await hass.config_entries.async_reload(mock_config_entry.entry_id)
    assert state_of(mock_config_entry) is ConfigEntryState.LOADED


async def test_missing_meter_is_reported(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    """A selected meter that no longer exists is listed as missing."""
    meters = load_fixture("meters.json")
    assert isinstance(meters, list)
    aioclient_mock.get(RECORD_URL, json=load_fixture("record.json"))
    aioclient_mock.get(METERS_URL, json=meters[:1])
    aioclient_mock.get(DATA_URL_PATTERN, json=load_fixture("data_pt1h_3d.json"))

    await setup_entry(hass, mock_config_entry)

    coordinator = mock_config_entry.runtime_data
    assert isinstance(coordinator, EnergyIDCoordinator)
    assert coordinator.data.missing_meter_ids == frozenset({EXPORT_METER_ID})


@pytest.mark.parametrize("mock_kwargs", [{"exc": TimeoutError()}, {"text": "x"}])
async def test_setup_retries_on_connection_or_api_error(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
    mock_kwargs: dict[str, TimeoutError | str],
) -> None:
    """An unreachable API or a malformed answer gives ConfigEntryNotReady."""
    exc = mock_kwargs.get("exc")
    text = mock_kwargs.get("text")
    aioclient_mock.get(
        RECORD_URL,
        exc=exc if isinstance(exc, BaseException) else None,
        text=text if isinstance(text, str) else None,
    )

    await setup_entry(hass, mock_config_entry)

    assert state_of(mock_config_entry) is ConfigEntryState.SETUP_RETRY


@pytest.mark.allow_warnings
async def test_setup_auth_failure_starts_reauth(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    """A 401 at setup gives ConfigEntryAuthFailed and starts reauth."""
    aioclient_mock.get(RECORD_URL, status=401)
    aioclient_mock.get(RECORDS_URL, json=load_fixture("records.json"))

    await setup_entry(hass, mock_config_entry)

    assert state_of(mock_config_entry) is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress()
    assert [flow["context"]["source"] for flow in flows] == [SOURCE_REAUTH]
