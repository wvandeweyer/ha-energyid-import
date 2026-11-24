"""Tests for the statistics sync against a real in-memory recorder."""

from datetime import UTC, datetime
from typing import Final

from freezegun.api import FrozenDateTimeFactory
from homeassistant.components.recorder.statistics import (
    StatisticsRow,
    statistics_during_period,
)
from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers.recorder import get_instance
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.components.recorder.common import (
    async_wait_recording_done,
)
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
)
from syrupy.assertion import SnapshotAssertion

from custom_components.energyid_import.api import JsonValue
from custom_components.energyid_import.const import API_BASE_URL
from custom_components.energyid_import.coordinator import EnergyIDCoordinator

from .conftest import DATA_URL_PATTERN, load_fixture, load_list_fixture
from .const import EXPORT_METER_ID, IMPORT_METER_ID, METERS_URL, RECORD_URL

NOW: Final = "2026-11-24 12:00:00+00:00"
IMPORT_ID: Final = "energyid_import:meter_dummy_meter_1"
EXPORT_ID: Final = "energyid_import:meter_dummy_meter_3"
IMPORT_DATA_URL: Final = f"{API_BASE_URL}/meters/{IMPORT_METER_ID}/data"


def data(*overrides: tuple[int, float]) -> dict[str, JsonValue]:
    """Return the recorded 3-day hourly data with some values replaced."""
    payload = load_fixture("data_pt1h_3d.json")
    assert isinstance(payload, dict)
    points = payload["data"]
    assert isinstance(points, list)
    for index, value in overrides:
        point = points[index]
        assert isinstance(point, dict)
        point["total"] = value
    return payload


def mock_record(aioclient_mock: AiohttpClientMocker, record: JsonValue = None) -> None:
    """Answer the record and meter endpoints."""
    aioclient_mock.get(RECORD_URL, json=record or load_fixture("record.json"))
    aioclient_mock.get(METERS_URL, json=load_fixture("meters.json"))


async def setup(hass: HomeAssistant, entry: MockConfigEntry) -> EnergyIDCoordinator:
    """Set up the entry and return its coordinator."""
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    coordinator = entry.runtime_data
    assert isinstance(coordinator, EnergyIDCoordinator)
    return coordinator


async def stored(hass: HomeAssistant, statistic_id: str) -> list[StatisticsRow]:
    """Return every stored hourly row of a statistic."""
    await async_wait_recording_done(hass)
    result = await get_instance(hass).async_add_executor_job(
        statistics_during_period,
        hass,
        datetime(2020, 1, 1, tzinfo=UTC),
        None,
        {statistic_id},
        "hour",
        None,
        {"state", "sum"},
    )
    return result.get(statistic_id, [])


def compact(rows: list[StatisticsRow]) -> list[tuple[str, float | None, float]]:
    """Reduce rows to (UTC start, state, rounded sum) for comparisons."""
    return [
        (
            datetime.fromtimestamp(row["start"], UTC).isoformat(),
            row.get("state"),
            round(row.get("sum") or 0.0, 6),
        )
        for row in rows
    ]


@pytest.fixture(autouse=True)
def frozen_time(freezer: FrozenDateTimeFactory) -> None:
    """Freeze time the day after the recorded data ends."""
    freezer.move_to(NOW)


@pytest.mark.usefixtures("mock_api")
async def test_first_sync_writes_hourly_statistics(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, snapshot: SnapshotAssertion
) -> None:
    """The backfill writes every past hour with a running sum."""
    coordinator = await setup(hass, mock_config_entry)

    rows = await stored(hass, IMPORT_ID)

    assert len(rows) == 72
    assert rows[-1].get("sum") == pytest.approx(36.9)
    assert compact(rows) == snapshot
    status = coordinator.data.statuses[IMPORT_METER_ID]
    assert status.written == 72
    assert status.latest_data == datetime(2026, 11, 23, 23, tzinfo=UTC)
    assert len(await stored(hass, EXPORT_ID)) == 72


@pytest.mark.usefixtures("mock_api")
async def test_second_sync_changes_nothing(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Two syncs on the same data give identical statistics."""
    coordinator = await setup(hass, mock_config_entry)
    first = compact(await stored(hass, IMPORT_ID))

    await coordinator.async_refresh()

    assert compact(await stored(hass, IMPORT_ID)) == first


async def test_correction_updates_hour_and_later_sums(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    """A value changed within the window changes its hour and every later sum."""
    mock_record(aioclient_mock)
    aioclient_mock.get(DATA_URL_PATTERN, json=data())
    coordinator = await setup(hass, mock_config_entry)
    before = compact(await stored(hass, IMPORT_ID))

    aioclient_mock.clear_requests()
    mock_record(aioclient_mock)
    aioclient_mock.get(DATA_URL_PATTERN, json=data((10, 5.0)))
    await coordinator.async_refresh()
    after = compact(await stored(hass, IMPORT_ID))

    assert after[:10] == before[:10]
    assert after[10][1] == 5.0
    shift = 5.0 - (before[10][1] or 0.0)
    assert [a[2] for a in after[10:]] == pytest.approx(
        [b[2] + shift for b in before[10:]]
    )


@pytest.mark.parametrize(
    ("response", "error"),
    [
        ((404, None, None), "not_found"),
        ((200, None, "not json"), "api_error"),
        ((200, TimeoutError(), None), "connection_error"),
    ],
)
@pytest.mark.allow_warnings
async def test_one_failing_meter_does_not_stop_others(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
    response: tuple[int, BaseException | None, str | None],
    error: str,
) -> None:
    """The failing meter gets an error status; the other is still written."""
    status, exc, text = response
    mock_record(aioclient_mock)
    aioclient_mock.get(IMPORT_DATA_URL, status=status, exc=exc, text=text)
    aioclient_mock.get(DATA_URL_PATTERN, json=data())

    coordinator = await setup(hass, mock_config_entry)

    assert coordinator.data.statuses[IMPORT_METER_ID].error == error
    assert coordinator.data.statuses[EXPORT_METER_ID].written == 72
    assert await stored(hass, IMPORT_ID) == []


@pytest.mark.allow_warnings
async def test_all_meters_failing_retries_setup(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    """When no meter can be synchronised, setup is retried later."""
    mock_record(aioclient_mock)
    aioclient_mock.get(DATA_URL_PATTERN, status=404)
    mock_config_entry.add_to_hass(hass)

    await hass.config_entries.async_setup(mock_config_entry.entry_id)

    assert mock_config_entry.state is ConfigEntryState.SETUP_RETRY


@pytest.mark.allow_warnings
async def test_auth_error_on_data_starts_reauth(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    """A 401 while fetching data stops the sync and asks for a new key."""
    mock_record(aioclient_mock)
    aioclient_mock.get(DATA_URL_PATTERN, status=401)
    mock_config_entry.add_to_hass(hass)

    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert mock_config_entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress()
    assert [flow["context"]["source"] for flow in flows] == [SOURCE_REAUTH]


@pytest.mark.allow_warnings
async def test_negative_values_are_skipped_with_one_warning(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Negative values are left out and reported once per meter."""
    mock_record(aioclient_mock)
    aioclient_mock.get(DATA_URL_PATTERN, json=data((0, -1.0), (1, -2.0)))

    await setup(hass, mock_config_entry)

    assert len(await stored(hass, IMPORT_ID)) == 70
    assert caplog.text.count("Skipped 2 negative values for meter dummy-meter-1") == 1


async def test_empty_response_writes_nothing(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    """An empty answer is not an error and writes no rows."""
    mock_record(aioclient_mock)
    aioclient_mock.get(DATA_URL_PATTERN, json={"data": []})

    coordinator = await setup(hass, mock_config_entry)

    status = coordinator.data.statuses[IMPORT_METER_ID]
    assert (status.written, status.latest_data, status.error) == (0, None, None)


async def test_daily_meter_is_fetched_per_day(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    """A meter with native P1D data is requested with interval P1D."""
    meters = load_list_fixture("meters.json")
    first = meters[0]
    assert isinstance(first, dict)
    first["interval"] = "P1D"
    aioclient_mock.get(RECORD_URL, json=load_fixture("record.json"))
    aioclient_mock.get(METERS_URL, json=meters)
    aioclient_mock.get(DATA_URL_PATTERN, json=load_fixture("data_p1d_14d.json"))

    await setup(hass, mock_config_entry)

    intervals = {
        url.query.get("interval")
        for _, url, _, _ in aioclient_mock.mock_calls
        if url.path.endswith(f"{IMPORT_METER_ID}/data")
    }
    assert intervals == {"P1D"}
    assert len(await stored(hass, IMPORT_ID)) == 14


async def test_record_without_time_zone_uses_home_assistant_zone(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Without a record time zone the sync uses Home Assistant's."""
    record = load_fixture("record.json")
    assert isinstance(record, dict)
    del record["timeZone"]
    mock_record(aioclient_mock, record)
    aioclient_mock.get(DATA_URL_PATTERN, json=data())

    await setup(hass, mock_config_entry)

    assert len(await stored(hass, IMPORT_ID)) == 72
