"""Tests for the pure statistics functions."""

from datetime import UTC, date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from custom_components.energyid_import.api import DataPoint, parse_data_point
from custom_components.energyid_import.const import Interval
from custom_components.energyid_import.statistics import (
    HourValue,
    StatisticRow,
    accumulate,
    merge,
    normalize,
    plan,
)

from .conftest import load_fixture

BRUSSELS = ZoneInfo("Europe/Brussels")
WINTER = timezone(timedelta(hours=1))
SUMMER = timezone(timedelta(hours=2))
LATER = datetime(2027, 1, 1, tzinfo=UTC)


def point(stamp: datetime, value: float | None) -> DataPoint:
    """Build one data point."""
    return DataPoint(timestamp=stamp, value=value)


def recorded(name: str) -> list[DataPoint]:
    """Parse the data points of a recorded fixture."""
    payload = load_fixture(name)
    assert isinstance(payload, dict)
    data = payload["data"]
    assert isinstance(data, list)
    return [parse_data_point(item) for item in data]


def hour(day: int, at: int) -> datetime:
    """Return a UTC hour in November 2026."""
    return datetime(2026, 11, day, at, tzinfo=UTC)


def test_quarters_sum_into_utc_hours() -> None:
    """Four quarters starting at local 00:00 form the hour 23:00 UTC."""
    start = datetime(2026, 11, 22, tzinfo=WINTER)
    points = [point(start + timedelta(minutes=15 * i), 0.1) for i in range(8)]

    result = normalize(points, interval=Interval.QUARTER_HOUR, now=LATER)

    assert [h.start for h in result.hours] == [hour(21, 23), hour(22, 0)]
    assert [h.state for h in result.hours] == pytest.approx([0.4, 0.4])


def test_recorded_quarters_match_recorded_hours() -> None:
    """The recorded PT15M fixture sums to 48 hours with the fixture's total."""
    result = normalize(
        recorded("data_pt15m_2d.json"), interval=Interval.QUARTER_HOUR, now=LATER
    )
    payload = load_fixture("data_pt15m_2d.json")
    assert isinstance(payload, dict)

    assert len(result.hours) == 48
    assert sum(h.state for h in result.hours) == pytest.approx(payload["total"])


def test_none_and_negative_values_are_skipped() -> None:
    """Missing values vanish silently; negatives are counted."""
    start = datetime(2026, 11, 22, 10, tzinfo=UTC)
    points = [point(start, None), point(start, -1.0), point(start, 2.0)]

    result = normalize(points, interval=Interval.HOUR, now=LATER)

    assert result.hours == (HourValue(start=start, state=2.0),)
    assert result.negative == 1


def test_running_hour_and_interval_are_left_out() -> None:
    """Quarters of a running hour and unfinished intervals are not written."""
    start = datetime(2026, 11, 24, 10, tzinfo=UTC)
    quarters = [point(start + timedelta(minutes=15 * i), 0.1) for i in range(4)]
    now = start + timedelta(minutes=40)

    assert normalize(quarters, interval=Interval.QUARTER_HOUR, now=now).hours == ()
    assert normalize(quarters, interval=Interval.HOUR, now=now).hours == ()


def test_daily_value_lands_at_local_midnight() -> None:
    """A P1D point is booked in the hour of local midnight once the day is over."""
    day = datetime(2026, 11, 23, tzinfo=WINTER)
    points = [point(day, 12.0)]

    late = normalize(points, interval=Interval.DAY, now=day + timedelta(hours=25))
    early = normalize(points, interval=Interval.DAY, now=day + timedelta(hours=20))

    assert late.hours == (HourValue(start=hour(22, 23), state=12.0),)
    assert early.hours == ()


def test_recorded_daily_fixture() -> None:
    """Every recorded day lands on 23:00 UTC in winter."""
    result = normalize(recorded("data_p1d_14d.json"), interval=Interval.DAY, now=LATER)

    assert len(result.hours) == 14
    assert {h.start.hour for h in result.hours} == {23}


def test_autumn_daylight_saving_day_has_25_hours() -> None:
    """25 October 2026 has two hours at 02:00 local, both kept."""
    result = normalize(
        recorded("data_pt1h_31d_dst.json"), interval=Interval.HOUR, now=LATER
    )
    day = [
        h
        for h in result.hours
        if h.start.astimezone(BRUSSELS).date() == date(2026, 10, 25)
    ]

    assert len(result.hours) == 745
    assert len(day) == 25
    assert len({h.start for h in result.hours}) == 745


def test_spring_daylight_saving_day_has_23_hours() -> None:
    """29 March 2026 has no 02:00 local; the day has 23 hours."""
    start = datetime(2026, 3, 29, tzinfo=WINTER)
    stamps = [start.astimezone(UTC) + timedelta(hours=i) for i in range(23)]
    points = [point(stamp.astimezone(BRUSSELS), 1.0) for stamp in stamps]

    result = normalize(points, interval=Interval.HOUR, now=LATER)

    assert len(result.hours) == 23
    assert result.hours[-1].start.astimezone(BRUSSELS) == datetime(
        2026, 3, 29, 23, tzinfo=SUMMER
    )


def rows(*states: float, base: float = 0.0, first: int = 0) -> tuple[StatisticRow, ...]:
    """Build consecutive rows from 1 November 2026 00:00 UTC."""
    hours = [
        HourValue(start=hour(1, 0) + timedelta(hours=first + i), state=s)
        for i, s in enumerate(states)
    ]
    return accumulate(hours, base_sum=base)


def test_first_backfill_starts_from_zero() -> None:
    """Without stored rows the sum starts at previous_sum."""
    fresh = rows(1.0, 2.0)

    result = plan(
        fresh=[HourValue(start=r.start, state=r.state) for r in fresh],
        existing=(),
        previous_sum=0.0,
    )

    assert [r.sum for r in result] == [1.0, 3.0]


def test_correction_inside_window_updates_later_sums() -> None:
    """A changed hour changes its state and every later sum."""
    stored = rows(1.0, 1.0, 1.0, base=10.0)
    corrected = [HourValue(start=stored[1].start, state=5.0)]

    result = plan(fresh=corrected, existing=stored, previous_sum=0.0)

    assert [r.state for r in result] == [1.0, 5.0, 1.0]
    assert [r.sum for r in result] == [11.0, 16.0, 17.0]


def test_gap_filled_later_is_repaired() -> None:
    """A missing hour that arrives later is inserted and the sums follow."""
    stored = (rows(1.0, base=10.0)[0], rows(1.0, base=11.0, first=2)[0])
    late = [HourValue(start=hour(1, 1), state=4.0)]

    result = plan(fresh=late, existing=stored, previous_sum=0.0)

    assert [r.start for r in result] == [hour(1, 0), hour(1, 1), hour(1, 2)]
    assert [r.sum for r in result] == [11.0, 15.0, 16.0]


def test_empty_response_keeps_stored_rows() -> None:
    """When the API returns nothing, the stored rows come back unchanged."""
    stored = rows(1.0, 2.0, base=5.0)

    assert plan(fresh=[], existing=stored, previous_sum=0.0) == stored
    assert plan(fresh=[], existing=(), previous_sum=7.0) == ()


def test_window_after_stored_rows_continues_previous_sum() -> None:
    """A window without stored rows continues from the last sum before it."""
    fresh = [HourValue(start=hour(3, 0), state=2.0)]

    result = plan(fresh=fresh, existing=(), previous_sum=40.0)

    assert result == (StatisticRow(start=hour(3, 0), state=2.0, sum=42.0),)


def test_merge_prefers_fresh_values() -> None:
    """Fresh hours replace stored ones; others stay."""
    stored = [
        HourValue(start=hour(1, 0), state=1.0),
        HourValue(start=hour(1, 1), state=1.0),
    ]
    fresh = [HourValue(start=hour(1, 1), state=3.0)]

    assert [h.state for h in merge(stored, fresh)] == [1.0, 3.0]
