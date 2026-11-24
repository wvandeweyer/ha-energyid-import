"""Tests for the repair issues."""

from datetime import UTC, datetime, timedelta
from typing import Final

from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
)

from custom_components.energyid_import.const import DOMAIN
from custom_components.energyid_import.coordinator import (
    ERROR_API,
    EnergyIDCoordinator,
    MeterStatus,
    active_issue,
)

from .conftest import DATA_URL_PATTERN, load_fixture, load_list_fixture
from .const import EXPORT_METER_ID, IMPORT_METER_ID, METERS_URL, RECORD_URL

NOW: Final = datetime(2026, 11, 24, 12, tzinfo=UTC)


def status(latest: datetime | None, error: str | None = None) -> MeterStatus:
    """Build a meter status."""
    return MeterStatus(latest_data=latest, written=0, error=error)


@pytest.mark.parametrize(
    ("meter_status", "missing", "expected"),
    [
        (status(NOW - timedelta(days=1)), False, None),
        (status(NOW - timedelta(days=4, hours=1)), False, "stale_data"),
        (status(None), False, "no_data"),
        (status(None, "not_found"), False, "meter_removed"),
        (None, True, "meter_removed"),
        (status(None, ERROR_API), False, None),
        (None, False, None),
    ],
)
def test_active_issue(
    *, meter_status: MeterStatus | None, missing: bool, expected: str | None
) -> None:
    """Each meter state maps to at most one issue."""
    assert active_issue(meter_status, missing=missing, now=NOW) == expected


def issue_ids(hass: HomeAssistant) -> set[str]:
    """Return the ids of this integration's open repair issues."""
    registry = ir.async_get(hass)
    return {key[1] for key in registry.issues if key[0] == DOMAIN}


def mock_meters(aioclient_mock: AiohttpClientMocker, count: int) -> None:
    """Answer the record with the first count recorded meters."""
    aioclient_mock.get(RECORD_URL, json=load_fixture("record.json"))
    aioclient_mock.get(METERS_URL, json=load_list_fixture("meters.json")[:count])


async def test_issues_follow_the_sync(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Issues appear for missing and stale meters and clear when fixed."""
    freezer.move_to("2026-12-01 12:00:00+00:00")
    mock_meters(aioclient_mock, 1)
    aioclient_mock.get(DATA_URL_PATTERN, json=load_fixture("data_pt1h_3d.json"))
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert issue_ids(hass) == {
        f"stale_data_{IMPORT_METER_ID}",
        f"meter_removed_{EXPORT_METER_ID}",
    }

    freezer.move_to("2026-11-24 12:00:00+00:00")
    aioclient_mock.clear_requests()
    mock_meters(aioclient_mock, 3)
    aioclient_mock.get(DATA_URL_PATTERN, json=load_fixture("data_pt1h_3d.json"))
    coordinator = mock_config_entry.runtime_data
    assert isinstance(coordinator, EnergyIDCoordinator)
    await coordinator.async_refresh()

    assert issue_ids(hass) == set()


async def test_no_data_issue_and_removal(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    """A meter without data gets an issue that goes with the entry."""
    mock_meters(aioclient_mock, 3)
    aioclient_mock.get(DATA_URL_PATTERN, json={"data": []})
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert issue_ids(hass) == {
        f"no_data_{IMPORT_METER_ID}",
        f"no_data_{EXPORT_METER_ID}",
    }

    await hass.config_entries.async_remove(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert issue_ids(hass) == set()
