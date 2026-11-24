"""Tests for the sync and rebuild_statistics services."""

from typing import Final

from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import device_registry as dr
from homeassistant.setup import async_setup_component
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
)

from custom_components.energyid_import.const import DOMAIN

from .conftest import DATA_URL_PATTERN, load_fixture
from .const import IMPORT_METER_ID, METERS_URL, RECORD_ID, RECORD_URL

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


def device_id(hass: HomeAssistant, entry: MockConfigEntry, identifier: str) -> str:
    """Return the registry id of one of the entry's devices."""
    devices = dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)
    return next(d.id for d in devices if (DOMAIN, identifier) in d.identifiers)


def data_starts(aioclient_mock: AiohttpClientMocker, meter_id: str) -> list[str]:
    """Return the start dates requested for a meter's data, in order."""
    return [
        str(url.query.get("start"))
        for _, url, _, _ in aioclient_mock.mock_calls
        if url.path.endswith(f"/meters/{meter_id}/data")
    ]


@pytest.mark.usefixtures("mock_api")
async def test_sync_service_runs_a_sync(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    """The sync service fetches the record again."""
    await setup(hass, mock_config_entry)
    before = aioclient_mock.call_count

    await hass.services.async_call(
        DOMAIN, "sync", {"config_entry_id": mock_config_entry.entry_id}, blocking=True
    )

    assert aioclient_mock.call_count > before


async def test_sync_service_needs_a_loaded_entry(hass: HomeAssistant) -> None:
    """An unknown entry or another integration's entry is a validation error."""
    assert await async_setup_component(hass, DOMAIN, {})
    other = MockConfigEntry(domain="other")
    other.add_to_hass(hass)

    for entry_id in ("missing", other.entry_id):
        with pytest.raises(ServiceValidationError) as info:
            await hass.services.async_call(
                DOMAIN, "sync", {"config_entry_id": entry_id}, blocking=True
            )
        assert info.value.translation_key == "entry_not_loaded"


@pytest.mark.allow_warnings
async def test_sync_service_reports_a_failed_sync(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    """A sync that fails raises an error to the caller."""
    aioclient_mock.get(RECORD_URL, json=load_fixture("record.json"))
    aioclient_mock.get(METERS_URL, json=load_fixture("meters.json"))
    aioclient_mock.get(DATA_URL_PATTERN, json=load_fixture("data_pt1h_3d.json"))
    await setup(hass, mock_config_entry)
    aioclient_mock.clear_requests()
    aioclient_mock.get(RECORD_URL, status=404)

    with pytest.raises(HomeAssistantError) as info:
        await hass.services.async_call(
            DOMAIN,
            "sync",
            {"config_entry_id": mock_config_entry.entry_id},
            blocking=True,
        )

    assert info.value.translation_key == "sync_failed"


@pytest.mark.usefixtures("mock_api")
@pytest.mark.parametrize(
    ("start_date", "first_start"), [("2026-06-01", "2026-06-01"), (None, "2025-11-24")]
)
async def test_rebuild_starts_at_the_requested_date(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
    start_date: str | None,
    first_start: str,
) -> None:
    """A rebuild fetches from the date, or from the start of the backfill."""
    await setup(hass, mock_config_entry)
    meter = device_id(hass, mock_config_entry, f"{RECORD_ID}_{IMPORT_METER_ID}")
    aioclient_mock.mock_calls.clear()
    data = {"device_id": meter} | ({"start_date": start_date} if start_date else {})

    await hass.services.async_call(DOMAIN, "rebuild_statistics", data, blocking=True)

    assert data_starts(aioclient_mock, IMPORT_METER_ID)[0] == first_start


@pytest.mark.usefixtures("mock_api")
async def test_rebuild_needs_a_meter_device(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """The record device and unknown devices are rejected."""
    await setup(hass, mock_config_entry)
    record = device_id(hass, mock_config_entry, RECORD_ID)
    other = MockConfigEntry(domain="other")
    other.add_to_hass(hass)
    foreign = dr.async_get(hass).async_get_or_create(
        config_entry_id=other.entry_id, identifiers={("other", "x")}
    )

    for device in (record, "unknown", foreign.id):
        with pytest.raises(ServiceValidationError) as info:
            await hass.services.async_call(
                DOMAIN, "rebuild_statistics", {"device_id": device}, blocking=True
            )
        assert info.value.translation_key == "not_a_meter"
