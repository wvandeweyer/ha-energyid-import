"""Property-based tests for the statistics invariants of docs/SPEC.md 9.3."""

from datetime import UTC, datetime, timedelta, timezone
from itertools import pairwise
from random import Random

from hypothesis import given, settings, strategies as st
import pytest

from custom_components.energyid_import.api import DataPoint
from custom_components.energyid_import.const import Interval
from custom_components.energyid_import.statistics import (
    StatisticRow,
    hour_start,
    normalize,
    plan,
)

START = datetime(2026, 3, 28, tzinfo=UTC)
NOW = datetime(2027, 1, 1, tzinfo=UTC)
QUARTER = timedelta(minutes=15)
TOLERANCE = 1e-9

type Store = dict[datetime, StatisticRow]

offsets = st.sampled_from([timezone(timedelta(hours=h)) for h in (0, 1, 2)])
values = st.one_of(
    st.none(),
    st.floats(min_value=-1.0, max_value=10.0, allow_nan=False).map(
        lambda v: round(v, 3)
    ),
)


@st.composite
def point_lists(draw: st.DrawFn) -> list[DataPoint]:
    """Draw quarter-hour points over up to four days with unique timestamps."""
    slots = draw(st.lists(st.integers(0, 4 * 96 - 1), unique=True, max_size=120))
    return [
        DataPoint(
            timestamp=(START + slot * QUARTER).astimezone(draw(offsets)),
            value=draw(values),
        )
        for slot in slots
    ]


def sync(store: Store, points: list[DataPoint], window_from: datetime) -> None:
    """Replay one sync of a window against an in-memory store."""
    fresh = normalize(
        [p for p in points if p.timestamp >= window_from],
        interval=Interval.QUARTER_HOUR,
        now=NOW,
    )
    existing = tuple(store[s] for s in sorted(store) if s >= window_from)
    before = [store[s] for s in sorted(store) if s < window_from]
    previous = before[-1].sum if before else 0.0
    for row in plan(fresh=fresh.hours, existing=existing, previous_sum=previous):
        store[row.start] = row


def rows_of(store: Store) -> list[StatisticRow]:
    """Return the stored rows in time order."""
    return [store[s] for s in sorted(store)]


@settings(max_examples=150)
@given(point_lists())
def test_sum_never_decreases_and_matches_states(points: list[DataPoint]) -> None:
    """Invariants 1 and 2: monotonic sum equal to base plus all states."""
    store: Store = {}
    sync(store, points, START)
    result = rows_of(store)

    assert all(b.sum >= a.sum for a, b in pairwise(result))
    total = sum(r.state for r in result)
    assert (result[-1].sum if result else 0.0) == pytest.approx(total, abs=TOLERANCE)


@settings(max_examples=150)
@given(point_lists())
def test_hours_are_whole_unique_and_sorted(points: list[DataPoint]) -> None:
    """Invariant 3: each bucket starts on a whole UTC hour, once, in order."""
    starts = [
        h.start
        for h in normalize(points, interval=Interval.QUARTER_HOUR, now=NOW).hours
    ]

    assert all(s == hour_start(s) and s.tzinfo == UTC for s in starts)
    assert starts == sorted(set(starts))


@settings(max_examples=150)
@given(point_lists(), st.randoms(use_true_random=False))
def test_input_order_does_not_matter(points: list[DataPoint], rng: Random) -> None:
    """Invariant 4: shuffling the points gives the same hours."""
    shuffled = list(points)
    rng.shuffle(shuffled)

    first = normalize(points, interval=Interval.QUARTER_HOUR, now=NOW).hours
    second = normalize(shuffled, interval=Interval.QUARTER_HOUR, now=NOW).hours

    assert [h.start for h in first] == [h.start for h in second]
    assert [h.state for h in first] == pytest.approx([h.state for h in second])


@settings(max_examples=150)
@given(point_lists())
def test_second_identical_sync_changes_nothing(points: list[DataPoint]) -> None:
    """Invariant 5: processing the same data twice equals processing it once."""
    store: Store = {}
    sync(store, points, START)
    once = rows_of(store)
    sync(store, points, START)

    assert rows_of(store) == once


@settings(max_examples=150)
@given(point_lists(), st.integers(1, 4 * 24 - 1))
def test_two_halves_equal_one_sync(points: list[DataPoint], split_hour: int) -> None:
    """Invariant 6: syncing two halves in turn equals one sync of the whole."""
    split = START + timedelta(hours=split_hour)
    whole: Store = {}
    sync(whole, points, START)
    halves: Store = {}
    sync(halves, [p for p in points if p.timestamp < split], START)
    sync(halves, points, split)

    expected, actual = rows_of(whole), rows_of(halves)
    assert [r.start for r in actual] == [r.start for r in expected]
    assert [r.sum for r in actual] == pytest.approx(
        [r.sum for r in expected], abs=TOLERANCE
    )
