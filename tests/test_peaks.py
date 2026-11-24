"""Tests for the monthly quarter-hour peak."""

from datetime import UTC, date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from custom_components.energyid_import.api import DataPoint, parse_data_point
from custom_components.energyid_import.statistics import (
    month_start,
    monthly_peaks,
)

from .conftest import load_fixture

BRUSSELS = ZoneInfo("Europe/Brussels")
WINTER = timezone(timedelta(hours=1))
LATER = datetime(2027, 1, 1, tzinfo=UTC)


def quarter(local: datetime, value: float | None) -> DataPoint:
    """Build one quarter-hour point."""
    return DataPoint(timestamp=local, value=value)


def test_registers_are_summed_before_the_maximum() -> None:
    """Day and night quarters at the same moment count together."""
    at = datetime(2026, 11, 20, 18, 0, tzinfo=WINTER)
    day = [quarter(at, 0.5), quarter(at + timedelta(minutes=15), 0.9)]
    night = [quarter(at, 0.6)]

    peaks = monthly_peaks([day, night], time_zone=BRUSSELS, now=LATER)

    peak = peaks[date(2026, 11, 1)]
    assert list(peaks) == [date(2026, 11, 1)]
    assert peak.power == pytest.approx(4.4)
    assert peak.start == at.astimezone(UTC)


def test_months_follow_the_local_calendar() -> None:
    """A quarter just after local midnight on the 1st belongs to the new month."""
    first = datetime(2026, 12, 1, 0, 0, tzinfo=WINTER)
    points = [quarter(first - timedelta(minutes=15), 1.0), quarter(first, 2.0)]

    peaks = monthly_peaks([points], time_zone=BRUSSELS, now=LATER)

    assert peaks[date(2026, 11, 1)].power == 4.0
    assert peaks[date(2026, 12, 1)].power == 8.0
    assert peaks[date(2026, 12, 1)].start == datetime(2026, 11, 30, 23, tzinfo=UTC)


def test_missing_running_and_tied_quarters() -> None:
    """None is skipped, unfinished quarters wait, the earliest tie wins."""
    at = datetime(2026, 11, 24, 10, 0, tzinfo=WINTER)
    points = [
        quarter(at, 1.0),
        quarter(at + timedelta(minutes=15), 1.0),
        quarter(at + timedelta(minutes=30), None),
        quarter(at + timedelta(minutes=45), 5.0),
    ]
    now = at + timedelta(minutes=50)

    peaks = monthly_peaks([points], time_zone=BRUSSELS, now=now)

    assert peaks[date(2026, 11, 1)].start == at.astimezone(UTC)
    assert peaks[date(2026, 11, 1)].power == 4.0


def test_recorded_quarters() -> None:
    """The recorded fixture gives one November peak equal to its largest quarter."""
    payload = load_fixture("data_pt15m_2d.json")
    assert isinstance(payload, dict)
    data = payload["data"]
    assert isinstance(data, list)
    points = [parse_data_point(item) for item in data]

    peaks = monthly_peaks([points], time_zone=BRUSSELS, now=LATER)

    largest = max(p.value or 0.0 for p in points)
    assert list(peaks) == [date(2026, 11, 1)]
    assert peaks[date(2026, 11, 1)].power == pytest.approx(largest * 4)


def test_month_start() -> None:
    """The first day of this or an earlier month."""
    assert month_start(date(2026, 11, 24)) == date(2026, 11, 1)
    assert month_start(date(2026, 1, 31), months_back=1) == date(2025, 12, 1)
