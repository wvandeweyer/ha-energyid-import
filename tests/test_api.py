"""Tests for the EnergyID API client."""

from collections.abc import Awaitable, Callable
from datetime import UTC, date, datetime, timedelta, timezone

from aiohttp import ClientConnectionError
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
import pytest
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
    AiohttpClientMockResponse,
)
from yarl import URL

from custom_components.energyid_import.api import (
    DataPoint,
    EnergyIDApiError,
    EnergyIDAuthError,
    EnergyIDClient,
    EnergyIDConnectionError,
    EnergyIDError,
    EnergyIDNotFoundError,
    Meter,
    Record,
    split_windows,
)
from custom_components.energyid_import.const import API_BASE_URL, Interval, Unit

from .conftest import load_fixture
from .const import API_KEY, IMPORT_METER_ID, METERS_URL, RECORD_ID, RECORDS_URL

DATA_URL = f"{API_BASE_URL}/meters/{IMPORT_METER_ID}/data"
BRUSSELS_WINTER = timezone(timedelta(hours=1))


class FakeSleep:
    """Record requested delays instead of sleeping."""

    def __init__(self) -> None:
        """Start with no recorded delays."""
        self.delays: list[float] = []

    async def __call__(self, delay: float) -> None:
        """Record one delay."""
        self.delays.append(delay)


type SideEffect = Callable[[str, URL, object], Awaitable[AiohttpClientMockResponse]]


def responses(*items: AiohttpClientMockResponse | BaseException) -> SideEffect:
    """Return a side effect that answers successive requests in order."""
    queue = list(items)

    async def side_effect(
        _method: str, _url: URL, _data: object
    ) -> AiohttpClientMockResponse:
        item = queue.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item

    return side_effect


def reply(
    status: int = 200,
    *,
    json: object = None,
    text: str | None = None,
    headers: dict[str, str] | None = None,
) -> AiohttpClientMockResponse:
    """Build one fake response for RECORDS_URL."""
    return AiohttpClientMockResponse(
        "get", URL(RECORDS_URL), status=status, json=json, text=text, headers=headers
    )


@pytest.fixture
def sleep() -> FakeSleep:
    """Return a sleep recorder."""
    return FakeSleep()


@pytest.fixture
def client(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, sleep: FakeSleep
) -> EnergyIDClient:
    """Return a client on the mocked session."""
    assert aioclient_mock.call_count == 0
    return EnergyIDClient(async_get_clientsession(hass), API_KEY, sleep=sleep)


async def assert_raises_without_key(
    client: EnergyIDClient, error: type[EnergyIDError]
) -> None:
    """Assert that fetching records raises error and never shows the key."""
    with pytest.raises(error) as info:
        await client.async_get_records()
    assert API_KEY not in str(info.value)
    assert API_KEY not in repr(info.value.__cause__)


async def test_records_send_api_key_header(
    client: EnergyIDClient, aioclient_mock: AiohttpClientMocker
) -> None:
    """Every request carries the apiKey authorization header."""
    aioclient_mock.get(RECORDS_URL, json=load_fixture("records.json"))

    records = await client.async_get_records()

    assert records == [
        Record(id=RECORD_ID, display_name="Dummy Home", time_zone="Europe/Brussels")
    ]
    headers = aioclient_mock.mock_calls[0][3]
    assert headers is not None
    assert headers["Authorization"] == f"apiKey {API_KEY}"


@pytest.mark.allow_warnings
async def test_records_skip_invalid_items(
    client: EnergyIDClient,
    aioclient_mock: AiohttpClientMocker,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A record without its required fields is skipped with a warning."""
    aioclient_mock.get(
        RECORDS_URL,
        json=[
            {"id": 42, "displayName": "Dummy Numeric"},
            {"id": True, "displayName": "Dummy Bool"},
            {"id": "dummy-record-9"},
            "not an object",
        ],
    )

    records = await client.async_get_records()

    assert records == [Record(id="42", display_name="Dummy Numeric", time_zone=None)]
    assert caplog.text.count("Skipping record") == 3


async def test_record(
    client: EnergyIDClient, aioclient_mock: AiohttpClientMocker
) -> None:
    """A single record is fetched by id."""
    aioclient_mock.get(
        f"{API_BASE_URL}/records/{RECORD_ID}", json=load_fixture("record.json")
    )

    record = await client.async_get_record(RECORD_ID)

    assert record.display_name == "Dummy Home"


async def test_meters(
    client: EnergyIDClient, aioclient_mock: AiohttpClientMocker
) -> None:
    """Meters are parsed into dataclasses with defaults for optional fields."""
    aioclient_mock.get(METERS_URL, json=load_fixture("meters.json"))

    meters = await client.async_get_meters(RECORD_ID)

    assert meters[0] == Meter(
        id=IMPORT_METER_ID,
        display_name="Dummy Grid import day",
        metric="electricityImport",
        unit=Unit.KILOWATT_HOUR,
        multiplier=1.0,
        register="high",
        interval=Interval.QUARTER_HOUR,
        connection_id="dummy-gridconnection-1",
        ean_suffix="0001",
        activated=None,
        closed=False,
        hidden=False,
    )
    assert [meter.register for meter in meters] == ["high", "low", "high", "low"]
    assert all(meter.importable for meter in meters)


async def test_meter_variants(
    client: EnergyIDClient, aioclient_mock: AiohttpClientMocker
) -> None:
    """Optional fields default; closed meters and unknown units are not importable."""
    base = {"id": "dummy-meter-9", "displayName": "Dummy", "metric": "m"}
    aioclient_mock.get(
        METERS_URL,
        json=[
            {**base, "unit": "m³", "interval": "PT10M"},
            {**base, "unit": "kWh", "closed": True, "hidden": True},
            {**base, "unit": "GJ"},
            {**base, "unit": "kWh", "activated": "2020-01-01T00:00:00+01:00"},
            {**base, "unit": "l"},
            {**base, "unit": "m³", "metric": "drinkingWaterImport"},
        ],
    )

    meters = await client.async_get_meters(RECORD_ID)

    assert [meter.importable for meter in meters] == [
        True,
        False,
        False,
        True,
        False,
        False,
    ]
    assert meters[0].multiplier == 1.0
    assert meters[0].register is None
    assert meters[0].interval is None
    assert meters[0].connection_id is None
    assert meters[0].ean_suffix is None
    assert meters[1].hidden
    assert meters[2].unit is None
    assert meters[3].activated == datetime(2020, 1, 1, tzinfo=BRUSSELS_WINTER)


@pytest.mark.allow_warnings
async def test_meters_skip_missing_required_fields(
    client: EnergyIDClient,
    aioclient_mock: AiohttpClientMocker,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A meter without id, displayName, unit or metric is skipped."""
    complete = {"id": "dummy-meter-1", "displayName": "Dummy", "metric": "m"}
    aioclient_mock.get(
        METERS_URL,
        json=[
            {**complete, "unit": "kWh"},
            complete,
            {"displayName": "Dummy", "metric": "m", "unit": "kWh"},
            {"id": "dummy-meter-2", "metric": "m", "unit": "kWh"},
            {"id": "dummy-meter-3", "displayName": "Dummy", "unit": "kWh"},
            {**complete, "unit": "kWh", "activated": "yesterday"},
        ],
    )

    meters = await client.async_get_meters(RECORD_ID)

    assert [meter.id for meter in meters] == ["dummy-meter-1"]
    assert caplog.text.count("Skipping meter") == 5


async def test_data_splits_windows_and_merges(
    client: EnergyIDClient, aioclient_mock: AiohttpClientMocker
) -> None:
    """A 40-day PT1H request is split at 31 days and duplicates are merged."""
    first = {
        "data": [
            {"timestamp": "2026-02-01T00:00:00+01:00", "total": 1.0},
            {"timestamp": "2026-01-31T23:00:00+01:00", "total": 2},
        ]
    }
    second = {
        "data": [
            {"timestamp": "2026-02-01T00:00:00+01:00", "total": 3.0},
            {"timestamp": "2026-02-01T01:00:00+01:00", "total": None},
        ]
    }
    params = {"interval": Interval.HOUR.value}
    aioclient_mock.get(
        DATA_URL,
        params={**params, "start": "2026-01-01", "end": "2026-02-01"},
        json=first,
    )
    aioclient_mock.get(
        DATA_URL,
        params={**params, "start": "2026-02-01", "end": "2026-02-10"},
        json=second,
    )

    points = await client.async_get_data(
        IMPORT_METER_ID, date(2026, 1, 1), date(2026, 2, 10), Interval.HOUR
    )

    assert aioclient_mock.call_count == 2
    assert points == [
        DataPoint(timestamp=datetime(2026, 1, 31, 22, tzinfo=UTC), value=2.0),
        DataPoint(timestamp=datetime(2026, 1, 31, 23, tzinfo=UTC), value=3.0),
        DataPoint(timestamp=datetime(2026, 2, 1, 0, tzinfo=UTC), value=None),
    ]


@pytest.mark.parametrize(
    ("name", "interval", "count"),
    [
        ("data_pt15m_2d.json", Interval.QUARTER_HOUR, 192),
        ("data_pt1h_3d.json", Interval.HOUR, 72),
        ("data_pt1h_31d_dst.json", Interval.HOUR, 745),
        ("data_p1d_14d.json", Interval.DAY, 14),
    ],
)
async def test_recorded_data(
    client: EnergyIDClient,
    aioclient_mock: AiohttpClientMocker,
    name: str,
    interval: Interval,
    count: int,
) -> None:
    """Recorded responses parse into unique, sorted points that sum to the total."""
    payload = load_fixture(name)
    assert isinstance(payload, dict)
    aioclient_mock.get(DATA_URL, json=payload)

    points = await client.async_get_data(
        IMPORT_METER_ID, date(2026, 10, 24), date(2026, 10, 25), interval
    )

    assert len(points) == count
    assert sum(point.value or 0.0 for point in points) == pytest.approx(
        payload["total"]
    )


@pytest.mark.parametrize(
    "payload",
    [
        [],
        {"data": {}},
        {"data": [{"total": 1.0}]},
        {"data": [{"timestamp": "2026-03-01T00:00:00", "total": 1.0}]},
        {"data": [{"timestamp": "not a date", "total": 1.0}]},
        {"data": ["not an object"]},
    ],
)
async def test_data_unexpected_structure(
    client: EnergyIDClient, aioclient_mock: AiohttpClientMocker, payload: object
) -> None:
    """A malformed data payload raises EnergyIDApiError."""
    aioclient_mock.get(DATA_URL, json=payload)

    with pytest.raises(EnergyIDApiError):
        await client.async_get_data(
            IMPORT_METER_ID, date(2026, 3, 1), date(2026, 3, 2), Interval.HOUR
        )


def test_split_windows() -> None:
    """Windows follow the per-interval maximum of the official client."""
    assert split_windows(date(2026, 1, 1), date(2026, 1, 1), Interval.HOUR) == []
    assert split_windows(
        date(2026, 1, 1), date(2026, 1, 10), Interval.QUARTER_HOUR
    ) == [
        (date(2026, 1, 1), date(2026, 1, 8)),
        (date(2026, 1, 8), date(2026, 1, 10)),
    ]
    assert split_windows(date(2025, 1, 1), date(2026, 1, 1), Interval.DAY) == [
        (date(2025, 1, 1), date(2026, 1, 1))
    ]
    with pytest.raises(ValueError, match="Unsupported interval"):
        split_windows(date(2026, 1, 1), date(2026, 1, 2), Interval.MONTH)


async def test_records_not_a_list(
    client: EnergyIDClient, aioclient_mock: AiohttpClientMocker
) -> None:
    """A records payload that is not a list raises EnergyIDApiError."""
    aioclient_mock.get(RECORDS_URL, json={"records": []})

    await assert_raises_without_key(client, EnergyIDApiError)


async def test_record_not_an_object(
    client: EnergyIDClient, aioclient_mock: AiohttpClientMocker
) -> None:
    """A record payload that is not an object raises EnergyIDApiError."""
    aioclient_mock.get(f"{API_BASE_URL}/records/{RECORD_ID}", json=[])

    with pytest.raises(EnergyIDApiError):
        await client.async_get_record(RECORD_ID)


async def test_invalid_json(
    client: EnergyIDClient, aioclient_mock: AiohttpClientMocker
) -> None:
    """A body that is not JSON raises EnergyIDApiError."""
    aioclient_mock.get(RECORDS_URL, text="<html>maintenance</html>")

    await assert_raises_without_key(client, EnergyIDApiError)


@pytest.mark.parametrize("status", [401, 403])
async def test_auth_error_is_not_retried(
    client: EnergyIDClient,
    aioclient_mock: AiohttpClientMocker,
    sleep: FakeSleep,
    status: int,
) -> None:
    """401 and 403 raise EnergyIDAuthError at once."""
    aioclient_mock.get(RECORDS_URL, status=status)

    await assert_raises_without_key(client, EnergyIDAuthError)
    assert aioclient_mock.call_count == 1
    assert sleep.delays == []


@pytest.mark.parametrize(
    ("status", "error"), [(404, EnergyIDNotFoundError), (400, EnergyIDApiError)]
)
async def test_client_errors(
    client: EnergyIDClient,
    aioclient_mock: AiohttpClientMocker,
    status: int,
    error: type[EnergyIDError],
) -> None:
    """Other 4xx statuses raise EnergyIDApiError without retry."""
    aioclient_mock.get(RECORDS_URL, status=status)

    await assert_raises_without_key(client, error)
    assert aioclient_mock.call_count == 1


async def test_server_error_retries_then_succeeds(
    client: EnergyIDClient, aioclient_mock: AiohttpClientMocker, sleep: FakeSleep
) -> None:
    """5xx and connection errors get two retries with 2 s and 8 s backoff."""
    aioclient_mock.get(
        RECORDS_URL,
        side_effect=responses(
            reply(503), ClientConnectionError("reset"), reply(json=[])
        ),
    )

    assert await client.async_get_records() == []
    assert sleep.delays == [2.0, 8.0]


async def test_timeout_gives_up_after_retries(
    client: EnergyIDClient, aioclient_mock: AiohttpClientMocker, sleep: FakeSleep
) -> None:
    """A request that keeps timing out raises EnergyIDConnectionError."""
    aioclient_mock.get(RECORDS_URL, exc=TimeoutError())

    await assert_raises_without_key(client, EnergyIDConnectionError)
    assert aioclient_mock.call_count == 3
    assert sleep.delays == [2.0, 8.0]


async def test_rate_limit_waits_for_retry_after(
    client: EnergyIDClient, aioclient_mock: AiohttpClientMocker, sleep: FakeSleep
) -> None:
    """A 429 waits for Retry-After; unreadable or negative values fall back."""
    aioclient_mock.get(
        RECORDS_URL,
        side_effect=responses(
            reply(429, headers={"Retry-After": "3"}),
            reply(429, headers={"Retry-After": "-1"}),
            reply(json=[]),
        ),
    )

    assert await client.async_get_records() == []
    assert sleep.delays == [3.0, 0.0]


async def test_rate_limit_gives_up(
    client: EnergyIDClient, aioclient_mock: AiohttpClientMocker, sleep: FakeSleep
) -> None:
    """Three 429 responses in a row raise EnergyIDConnectionError."""
    aioclient_mock.get(RECORDS_URL, status=429)

    await assert_raises_without_key(client, EnergyIDConnectionError)
    assert aioclient_mock.call_count == 3
    assert sleep.delays == [5.0, 5.0]
