"""Thin async client for the EnergyID Web API.

Every public method returns dataclasses. Every failure surfaces as one of
EnergyIDAuthError, EnergyIDConnectionError or EnergyIDApiError.
"""

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta
import logging
from typing import Final
from urllib.parse import quote

from aiohttp import ClientError, ClientResponse, ClientSession

from .const import (
    API_BASE_URL,
    EXCLUDED_METRICS,
    MAX_RATE_LIMIT_ATTEMPTS,
    REQUEST_TIMEOUT,
    RETRY_BACKOFF,
    WINDOW_DAYS,
    Interval,
    Unit,
)

_LOGGER = logging.getLogger(__name__)

type JsonValue = (
    str | int | float | bool | list[JsonValue] | dict[str, JsonValue] | None
)
type JsonObject = dict[str, JsonValue]
type Params = Mapping[str, str]
type Sleep = Callable[[float], Awaitable[None]]

DEFAULT_RETRY_AFTER: Final = 5.0
EAN_SUFFIX_LENGTH: Final = 4
HTTP_SERVER_ERROR: Final = 500
HTTP_CLIENT_ERROR: Final = 400
HTTP_NOT_FOUND: Final = 404
HTTP_TOO_MANY_REQUESTS: Final = 429
AUTH_STATUSES: Final = frozenset({401, 403})


class EnergyIDError(Exception):
    """Base class for all errors raised by this client."""


class EnergyIDAuthError(EnergyIDError):
    """The API key is invalid or lacks the required permissions."""


class EnergyIDConnectionError(EnergyIDError):
    """The API could not be reached or answered with a server error."""


class EnergyIDApiError(EnergyIDError):
    """The API answered with an unexpected status or payload."""


class EnergyIDNotFoundError(EnergyIDApiError):
    """The requested resource does not exist."""


class _RateLimitedError(Exception):
    """Internal signal that the API asked us to back off."""

    def __init__(self, *, delay: float) -> None:
        """Store the delay requested by the Retry-After header."""
        super().__init__(f"Rate limited, retry after {delay} s")
        self.delay = delay


@dataclass(frozen=True, slots=True, kw_only=True)
class Record:
    """An EnergyID record (dossier)."""

    id: str
    display_name: str
    time_zone: str | None


@dataclass(frozen=True, slots=True, kw_only=True)
class Meter:
    """A meter inside a record."""

    id: str
    display_name: str
    metric: str
    unit: Unit | None
    multiplier: float
    register: str | None
    interval: Interval | None
    connection_id: str | None
    ean_suffix: str | None
    activated: datetime | None
    closed: bool
    hidden: bool

    @property
    def importable(self) -> bool:
        """Return whether this meter can be imported as a statistic."""
        return (
            not self.closed
            and self.unit is not None
            and self.metric not in EXCLUDED_METRICS
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class DataPoint:
    """Consumption of a meter in the interval that starts at timestamp."""

    timestamp: datetime
    value: float | None


def _as_object(value: JsonValue, *, what: str) -> JsonObject:
    """Return value as a JSON object or raise EnergyIDApiError."""
    if not isinstance(value, dict):
        msg = f"Expected an object for {what}"
        raise EnergyIDApiError(msg)
    return value


def _as_list(value: JsonValue, *, what: str) -> list[JsonValue]:
    """Return value as a JSON list or raise EnergyIDApiError."""
    if not isinstance(value, list):
        msg = f"Expected a list for {what}"
        raise EnergyIDApiError(msg)
    return value


def _require_id(obj: JsonObject, key: str) -> str:
    """Return an identifier field, which the API may send as string or integer."""
    value = obj.get(key)
    if isinstance(value, bool) or not isinstance(value, str | int):
        msg = f"Missing or invalid field {key}"
        raise EnergyIDApiError(msg)
    return str(value)


def _require_str(obj: JsonObject, key: str) -> str:
    """Return a mandatory string field."""
    value = obj.get(key)
    if not isinstance(value, str):
        msg = f"Missing or invalid field {key}"
        raise EnergyIDApiError(msg)
    return value


def _optional_str(obj: JsonObject, key: str) -> str | None:
    """Return an optional string field."""
    value = obj.get(key)
    return value if isinstance(value, str) else None


def _optional_number(value: JsonValue) -> float | None:
    """Return a JSON number as float, or None for anything else."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def _parse_datetime(value: str) -> datetime:
    """Parse an ISO 8601 timestamp that must carry an offset."""
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as err:
        msg = "Invalid timestamp"
        raise EnergyIDApiError(msg) from err
    if parsed.tzinfo is None:
        msg = "Timestamp without offset"
        raise EnergyIDApiError(msg)
    return parsed


def _optional_datetime(obj: JsonObject, key: str) -> datetime | None:
    """Return an optional timestamp field."""
    value = _optional_str(obj, key)
    return None if value is None else _parse_datetime(value)


def _parse_interval(value: str | None) -> Interval | None:
    """Return the meter's native interval, or None when absent or unknown."""
    try:
        return None if value is None else Interval(value)
    except ValueError:
        return None


def _parse_unit(value: str) -> Unit | None:
    """Return the known unit, or None when the integration cannot import it."""
    try:
        return Unit(value)
    except ValueError:
        return None


def _ean_suffix(obj: JsonObject) -> str | None:
    """Return the last four characters of the EAN; the full code is never kept."""
    ean = _optional_str(obj, "connectionNumber")
    return ean[-EAN_SUFFIX_LENGTH:] if ean else None


def parse_record(value: JsonValue) -> Record:
    """Parse one record object."""
    obj = _as_object(value, what="record")
    return Record(
        id=_require_id(obj, "id"),
        display_name=_require_str(obj, "displayName"),
        time_zone=_optional_str(obj, "timeZone"),
    )


def parse_meter(value: JsonValue) -> Meter:
    """Parse one meter object."""
    obj = _as_object(value, what="meter")
    multiplier = _optional_number(obj.get("multiplier"))
    return Meter(
        id=_require_id(obj, "id"),
        display_name=_require_str(obj, "displayName"),
        metric=_require_str(obj, "metric"),
        unit=_parse_unit(_require_str(obj, "unit")),
        multiplier=1.0 if multiplier is None else multiplier,
        register=_optional_str(obj, "register"),
        interval=_parse_interval(_optional_str(obj, "interval")),
        connection_id=_optional_str(obj, "gridConnectionId"),
        ean_suffix=_ean_suffix(obj),
        activated=_optional_datetime(obj, "activated"),
        closed=obj.get("closed") is True,
        hidden=obj.get("hidden") is True,
    )


def parse_data_point(value: JsonValue) -> DataPoint:
    """Parse one data point."""
    obj = _as_object(value, what="data point")
    return DataPoint(
        timestamp=_parse_datetime(_require_str(obj, "timestamp")),
        value=_optional_number(obj.get("total")),
    )


def _parse_each[T](
    values: list[JsonValue], parser: Callable[[JsonValue], T], *, what: str
) -> list[T]:
    """Parse every item, skipping and logging items that fail."""
    parsed: list[T] = []
    for value in values:
        try:
            parsed.append(parser(value))
        except EnergyIDApiError as err:
            _LOGGER.warning("Skipping %s: %s", what, err)
    return parsed


def split_windows(
    start: date, end: date, interval: Interval
) -> list[tuple[date, date]]:
    """Split a date range into the windows the API accepts for an interval."""
    if interval not in WINDOW_DAYS:
        msg = f"Unsupported interval {interval}"
        raise ValueError(msg)
    size = timedelta(days=WINDOW_DAYS[interval])
    windows: list[tuple[date, date]] = []
    cursor = start
    while cursor < end:
        window_end = min(cursor + size, end)
        windows.append((cursor, window_end))
        cursor = window_end
    return windows


def _retry_after(response: ClientResponse) -> float:
    """Return the delay in seconds requested by a 429 response."""
    try:
        return max(float(response.headers.get("Retry-After", "")), 0.0)
    except ValueError:
        return DEFAULT_RETRY_AFTER


async def _async_read_json(response: ClientResponse) -> JsonValue:
    """Translate a response into JSON or one of the client's exceptions."""
    status = response.status
    if status in AUTH_STATUSES:
        msg = f"Authentication failed with status {status}"
        raise EnergyIDAuthError(msg)
    if status == HTTP_TOO_MANY_REQUESTS:
        raise _RateLimitedError(delay=_retry_after(response))
    if status >= HTTP_SERVER_ERROR:
        msg = f"Server error {status}"
        raise EnergyIDConnectionError(msg)
    if status == HTTP_NOT_FOUND:
        msg = "Resource not found"
        raise EnergyIDNotFoundError(msg)
    if status >= HTTP_CLIENT_ERROR:
        msg = f"Unexpected status {status}"
        raise EnergyIDApiError(msg)
    try:
        payload: JsonValue = await response.json(content_type=None)
    except ValueError as err:
        msg = "Invalid JSON in response"
        raise EnergyIDApiError(msg) from err
    return payload


class EnergyIDClient:
    """Read-only client for the EnergyID Web API using a personal API key."""

    def __init__(
        self,
        session: ClientSession,
        api_key: str,
        *,
        base_url: str = API_BASE_URL,
        retry_backoff: tuple[float, ...] = RETRY_BACKOFF,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        """Initialize the client with an injected aiohttp session.

        Args:
            session: The aiohttp session to send requests on.
            api_key: Personal EnergyID API key with read access.
            base_url: Root URL of the Web API.
            retry_backoff: Delays before each retry of a connection or server
                error; empty to fail fast.
            sleep: Coroutine used to wait between retries.
        """
        self._session = session
        self._headers = {
            "Authorization": f"apiKey {api_key}",
            "Accept": "application/json",
        }
        self._base_url = base_url
        self._retry_backoff = retry_backoff
        self._sleep = sleep

    async def async_get_records(self) -> list[Record]:
        """Return the records the API key has access to."""
        payload = await self._async_get_json("members/me/records")
        records = _as_list(payload, what="records")
        return _parse_each(records, parse_record, what="record")

    async def async_get_record(self, record_id: str) -> Record:
        """Return one record."""
        payload = await self._async_get_json(f"records/{quote(record_id, safe='')}")
        return parse_record(payload)

    async def async_get_meters(self, record_id: str) -> list[Meter]:
        """Return the meters of a record."""
        path = f"records/{quote(record_id, safe='')}/meters"
        meters = _as_list(await self._async_get_json(path), what="meters")
        return _parse_each(meters, parse_meter, what="meter")

    async def async_get_data(
        self, meter_id: str, start: date, end: date, interval: Interval
    ) -> list[DataPoint]:
        """Return the data points of a meter, fetched window by window.

        The end date is exclusive. Points that appear in two windows are kept
        once, sorted by timestamp.
        """
        path = f"meters/{quote(meter_id, safe='')}/data"
        points: dict[datetime, DataPoint] = {}
        for window_start, window_end in split_windows(start, end, interval):
            params = {
                "start": window_start.isoformat(),
                "end": window_end.isoformat(),
                "interval": interval.value,
            }
            payload = _as_object(
                await self._async_get_json(path, params), what="meter data"
            )
            for value in _as_list(payload.get("data"), what="meter data"):
                point = parse_data_point(value)
                points[point.timestamp] = point
        return [points[timestamp] for timestamp in sorted(points)]

    async def _async_get_json(
        self, path: str, params: Params | None = None
    ) -> JsonValue:
        """Perform a GET with retries on server errors and rate limiting."""
        failures = 0
        rate_limited = 0
        while True:
            try:
                return await self._async_get_once(path, params)
            except _RateLimitedError as err:
                rate_limited += 1
                if rate_limited >= MAX_RATE_LIMIT_ATTEMPTS:
                    msg = "Rate limit persisted after retries"
                    raise EnergyIDConnectionError(msg) from err
                await self._sleep(err.delay)
            except EnergyIDConnectionError:
                if failures >= len(self._retry_backoff):
                    raise
                await self._sleep(self._retry_backoff[failures])
                failures += 1

    async def _async_get_once(self, path: str, params: Params | None) -> JsonValue:
        """Perform a single GET within the request timeout."""
        url = f"{self._base_url}/{path}"
        request = self._session.get(url, headers=self._headers, params=params)
        try:
            async with asyncio.timeout(REQUEST_TIMEOUT), request as response:
                return await _async_read_json(response)
        except TimeoutError as err:
            msg = "Request timed out"
            raise EnergyIDConnectionError(msg) from err
        except ClientError as err:
            msg = f"Connection error: {type(err).__name__}"
            raise EnergyIDConnectionError(msg) from err
