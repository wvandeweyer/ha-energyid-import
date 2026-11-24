"""Tests for the monthly peak sensors."""

from collections.abc import Awaitable, Callable
from typing import Final

from freezegun.api import FrozenDateTimeFactory
from homeassistant.const import STATE_UNKNOWN
from homeassistant.core import HomeAssistant
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
    AiohttpClientMockResponse,
)
from yarl import URL

from custom_components.energyid_import.api import JsonValue

from .conftest import DATA_URL_PATTERN, load_fixture, load_list_fixture
from .const import METERS_URL, RECORD_URL

NOW: Final = "2026-11-24 12:00:00+00:00"
CURRENT: Final = "sensor.dummy_home_peak_this_month"
PREVIOUS: Final = "sensor.dummy_home_peak_last_month"


@pytest.fixture(autouse=True)
def frozen_time(freezer: FrozenDateTimeFactory) -> None:
    """Freeze time the day after the recorded data ends."""
    freezer.move_to(NOW)


type SideEffect = Callable[[str, URL, object], Awaitable[AiohttpClientMockResponse]]


def by_interval(quarter_status: int = 200) -> SideEffect:
    """Answer quarter-hour requests with PT15M data, others with PT1H data."""

    async def side_effect(
        method: str, url: URL, _data: object
    ) -> AiohttpClientMockResponse:
        quarter = url.query.get("interval") == "PT15M"
        name = "data_pt15m_2d.json" if quarter else "data_pt1h_3d.json"
        status = quarter_status if quarter else 200
        return AiohttpClientMockResponse(
            method, url, status=status, json=load_fixture(name)
        )

    return side_effect


def mock_record(aioclient_mock: AiohttpClientMocker, meters: JsonValue) -> None:
    """Answer the record and meter endpoints."""
    aioclient_mock.get(RECORD_URL, json=load_fixture("record.json"))
    aioclient_mock.get(METERS_URL, json=meters)


async def setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """Set up the entry."""
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def test_peak_sums_both_import_registers(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    """This month's peak adds day and night quarters; last month is unknown."""
    mock_record(aioclient_mock, load_list_fixture("meters.json"))
    aioclient_mock.get(DATA_URL_PATTERN, side_effect=by_interval())

    await setup(hass, mock_config_entry)

    payload = load_fixture("data_pt15m_2d.json")
    assert isinstance(payload, dict)
    data = payload["data"]
    assert isinstance(data, list)
    largest = max(
        float(p["total"])
        for p in data
        if isinstance(p, dict) and isinstance(p["total"], int | float)
    )
    current = hass.states.get(CURRENT)
    assert current is not None
    assert float(current.state) == pytest.approx(largest * 2 * 4)
    assert current.attributes["unit_of_measurement"] == "kW"
    assert current.attributes["peak_start"].startswith("2026-11-2")
    previous = hass.states.get(PREVIOUS)
    assert previous is not None
    assert previous.state == STATE_UNKNOWN


async def test_no_peak_sensors_without_quarter_hour_data(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    """An import meter with daily data leaves the peak out."""
    meters = load_list_fixture("meters.json")
    night = meters[1]
    assert isinstance(night, dict)
    night["interval"] = "P1D"
    mock_record(aioclient_mock, meters)
    aioclient_mock.get(DATA_URL_PATTERN, side_effect=by_interval())

    await setup(hass, mock_config_entry)

    assert hass.states.get(CURRENT) is None


@pytest.mark.allow_warnings
async def test_failed_quarter_hour_fetch_keeps_the_sync(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A failing peak fetch is logged; statistics and other sensors stay."""
    mock_record(aioclient_mock, load_list_fixture("meters.json"))
    aioclient_mock.get(DATA_URL_PATTERN, side_effect=by_interval(quarter_status=400))

    await setup(hass, mock_config_entry)

    assert hass.states.get(CURRENT) is None
    assert hass.states.get("sensor.dummy_home_last_sync") is not None
    assert "Could not fetch quarter-hour data for the peak" in caplog.text
