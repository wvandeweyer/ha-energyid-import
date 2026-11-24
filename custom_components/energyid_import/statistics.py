"""Pure functions that turn EnergyID data points into hourly statistics.

Nothing here imports Home Assistant or reads the clock: `now` and `today` are
arguments. Each sync rewrites a contiguous window of hours with a sum that is
computed again from the hour before the window.
"""

from calendar import monthrange
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta, tzinfo
import re
from typing import Final, Protocol

from .const import (
    DOMAIN,
    INTERVAL_DURATION,
    REFETCH_DAYS,
    STATISTIC_PREFIX,
    UNIT_CLASSES,
    Interval,
    Unit,
)

ONE_HOUR: Final = timedelta(hours=1)
MONTHS_PER_YEAR: Final = 12
_NOT_SLUG: Final = re.compile(r"[^a-z0-9]+")


class Point(Protocol):
    """A consumption value for the interval that starts at timestamp."""

    @property
    def timestamp(self) -> datetime:
        """Return the timezone-aware start of the interval."""
        ...

    @property
    def value(self) -> float | None:
        """Return the consumption, or None when the source has no value."""
        ...


@dataclass(frozen=True, slots=True, kw_only=True)
class HourValue:
    """Consumption in the hour that starts at start, in UTC."""

    start: datetime
    state: float


@dataclass(frozen=True, slots=True, kw_only=True)
class StatisticRow:
    """One hourly statistic row with its running sum."""

    start: datetime
    state: float
    sum: float


@dataclass(frozen=True, slots=True, kw_only=True)
class Normalized:
    """Complete hours from a batch of points and the negatives left out."""

    hours: tuple[HourValue, ...]
    negative: int


@dataclass(frozen=True, slots=True, kw_only=True)
class StatisticSpec:
    """Identity and unit of the statistic for one meter."""

    statistic_id: str
    name: str
    unit: str
    unit_class: str


def hour_start(moment: datetime) -> datetime:
    """Return the start of the UTC hour that contains moment."""
    return moment.astimezone(UTC).replace(minute=0, second=0, microsecond=0)


def normalize(
    points: Iterable[Point], *, interval: Interval, now: datetime
) -> Normalized:
    """Sum points into UTC hours, keeping only intervals and hours that are over.

    Points without a value are skipped; negative values are skipped and counted.
    A daily point lands in the hour of local midnight.
    """
    duration = INTERVAL_DURATION[interval]
    totals: dict[datetime, float] = {}
    negative = 0
    for point in points:
        value = point.value
        if value is None or point.timestamp + duration > now:
            continue
        if value < 0:
            negative += 1
            continue
        hour = hour_start(point.timestamp)
        totals[hour] = totals.get(hour, 0.0) + value
    hours = tuple(
        HourValue(start=start, state=totals[start])
        for start in sorted(totals)
        if start + ONE_HOUR <= now
    )
    return Normalized(hours=hours, negative=negative)


def merge(
    existing: Iterable[HourValue], fresh: Iterable[HourValue]
) -> tuple[HourValue, ...]:
    """Overlay fresh hours on existing ones; hours missing from fresh are kept."""
    combined = {hour.start: hour for hour in existing}
    combined.update((hour.start, hour) for hour in fresh)
    return tuple(combined[start] for start in sorted(combined))


def accumulate(
    hours: Iterable[HourValue], *, base_sum: float
) -> tuple[StatisticRow, ...]:
    """Attach a running sum that starts from base_sum."""
    rows: list[StatisticRow] = []
    running = base_sum
    for hour in hours:
        running += hour.state
        rows.append(StatisticRow(start=hour.start, state=hour.state, sum=running))
    return tuple(rows)


def plan(
    *,
    fresh: Iterable[HourValue],
    existing: Sequence[StatisticRow],
    previous_sum: float,
) -> tuple[StatisticRow, ...]:
    """Return the rows to write for a window.

    Args:
        fresh: Hours just fetched for the window.
        existing: Rows already stored from the window start onwards, sorted.
        previous_sum: Sum of the last stored row before the window, or 0.0.
    """
    base_sum = existing[0].sum - existing[0].state if existing else previous_sum
    current = (HourValue(start=row.start, state=row.state) for row in existing)
    return accumulate(merge(current, fresh), base_sum=base_sum)


def subtract_months(day: date, months: int) -> date:
    """Return day moved back by whole months, clamped to the month's last day."""
    index = day.year * MONTHS_PER_YEAR + day.month - 1 - months
    year, month = divmod(index, MONTHS_PER_YEAR)
    return day.replace(
        year=year, month=month + 1, day=min(day.day, monthrange(year, month + 1)[1])
    )


def window_start(
    *,
    last_start: datetime | None,
    today: date,
    time_zone: tzinfo,
    backfill_months: int,
) -> date:
    """Return the first local date a sync fetches.

    Without stored statistics the backfill reaches back backfill_months; with
    them the window reaches REFETCH_DAYS behind the last stored hour.
    """
    if last_start is None:
        return subtract_months(today, backfill_months)
    refetch_from = last_start - timedelta(days=REFETCH_DAYS)
    return min(refetch_from.astimezone(time_zone).date(), today)


def local_midnight(day: date, time_zone: tzinfo) -> datetime:
    """Return the UTC moment at which a local date starts."""
    return datetime(day.year, day.month, day.day, tzinfo=time_zone).astimezone(UTC)


def statistic_id(meter_id: str) -> str:
    """Return a valid external statistic id for a meter id."""
    slug = _NOT_SLUG.sub("_", meter_id.lower()).strip("_")
    if not slug:
        msg = "Meter id has no characters usable in a statistic id"
        raise ValueError(msg)
    return f"{DOMAIN}:{STATISTIC_PREFIX}{slug}"


def statistic_name(
    record_name: str,
    meter_name: str,
    *,
    register: str | None,
    ean_suffix: str | None,
) -> str:
    """Return the display name, with register and EAN suffix when given."""
    details = [part for part in (register, ean_suffix and f"EAN …{ean_suffix}") if part]
    name = f"EnergyID {record_name} {meter_name}"
    return f"{name} ({', '.join(details)})" if details else name


def statistic_spec(meter_id: str, name: str, unit: Unit) -> StatisticSpec:
    """Return the statistic identity and unit for a meter."""
    return StatisticSpec(
        statistic_id=statistic_id(meter_id),
        name=name,
        unit=unit.value,
        unit_class=UNIT_CLASSES[unit],
    )
