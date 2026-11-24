"""Tests for the diagnostic sensors and devices."""

from typing import Final

from freezegun.api import FrozenDateTimeFactory
from homeassistant.const import STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
)
from syrupy.assertion import SnapshotAssertion

from custom_components.energyid_import.const import (
    CONF_BACKFILL_MONTHS,
    CONF_METER_IDS,
    DOMAIN,
)

from .conftest import DATA_URL_PATTERN, load_fixture
from .const import (
    EXPORT_METER_ID,
    IMPORT_METER_ID,
    METERS_URL,
    RECORD_ID,
    RECORD_URL,
)

NOW: Final = "2026-11-24 12:00:00+00:00"


@pytest.fixture(autouse=True)
def frozen_time(freezer: FrozenDateTimeFactory) -> None:
    """Freeze time the day after the recorded data ends."""
    freezer.move_to(NOW)


async def setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """Set up the entry."""
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


def device_by_identifier(
    hass: HomeAssistant, entry: MockConfigEntry, identifier: str
) -> dr.DeviceEntry | None:
    """Return the entry's device with an identifier, on every HA release."""
    devices = dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)
    matches = [d for d in devices if (DOMAIN, identifier) in d.identifiers]
    return matches[0] if matches else None


def entity_states(
    hass: HomeAssistant, entry: MockConfigEntry
) -> list[tuple[str, str, str | None]]:
    """Return (entity id, state, device name) for every entity of the entry."""
    entities = er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
    devices = dr.async_get(hass)
    result = []
    for entity in sorted(entities, key=lambda e: e.entity_id):
        state = hass.states.get(entity.entity_id)
        device = devices.async_get(entity.device_id) if entity.device_id else None
        result.append(
            (
                entity.entity_id,
                state.state if state else "missing",
                device.name if device else None,
            )
        )
    return result


@pytest.mark.usefixtures("mock_api")
async def test_sensors_and_devices(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, snapshot: SnapshotAssertion
) -> None:
    """The record and each selected meter get a device with diagnostic sensors."""
    await setup(hass, mock_config_entry)

    assert entity_states(hass, mock_config_entry) == snapshot
    record = device_by_identifier(hass, mock_config_entry, RECORD_ID)
    meter = device_by_identifier(
        hass, mock_config_entry, f"{RECORD_ID}_{IMPORT_METER_ID}"
    )
    assert record is not None
    assert meter is not None
    assert meter.via_device_id == record.id
    assert meter.serial_number == "EAN …0001"
    assert record.entry_type is dr.DeviceEntryType.SERVICE


async def test_latest_data_unknown_without_data(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    """A meter without any imported hour shows an unknown latest-data state."""
    aioclient_mock.get(RECORD_URL, json=load_fixture("record.json"))
    aioclient_mock.get(METERS_URL, json=load_fixture("meters.json"))
    aioclient_mock.get(DATA_URL_PATTERN, json={"data": []})

    await setup(hass, mock_config_entry)

    states = {e: s for e, s, _ in entity_states(hass, mock_config_entry)}
    latest = [s for e, s in states.items() if e.endswith("latest_data")]
    assert latest == [STATE_UNKNOWN, STATE_UNKNOWN]


@pytest.mark.usefixtures("mock_api")
async def test_deselected_meter_loses_its_device(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """After an options change the dropped meter's device is removed."""
    await setup(hass, mock_config_entry)
    export = f"{RECORD_ID}_{EXPORT_METER_ID}"
    assert device_by_identifier(hass, mock_config_entry, export) is not None

    hass.config_entries.async_update_entry(
        mock_config_entry,
        options={CONF_METER_IDS: [IMPORT_METER_ID], CONF_BACKFILL_MONTHS: 12},
    )
    await hass.config_entries.async_reload(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert device_by_identifier(hass, mock_config_entry, export) is None
    latest = [e for e, _, _ in entity_states(hass, mock_config_entry)]
    assert [e for e in latest if e.endswith("latest_data")] == [
        "sensor.dummy_grid_import_day_high_latest_data"
    ]
