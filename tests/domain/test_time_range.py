"""An Interval guards the calendar holds, so its edges are pinned down here."""

from datetime import UTC, datetime, timedelta, timezone

import pytest

from aeca.domain.time_range import Interval

BUCHAREST = timezone(timedelta(hours=2))
PARIS = timezone(timedelta(hours=1))


def test_a_naive_start_is_refused() -> None:
    with pytest.raises(ValueError, match="timezone"):
        Interval(start=datetime(2026, 11, 9, 15, 0), end=datetime(2026, 11, 9, 16, 0, tzinfo=UTC))


def test_a_naive_end_is_refused() -> None:
    with pytest.raises(ValueError, match="timezone"):
        Interval(start=datetime(2026, 11, 9, 15, 0, tzinfo=UTC), end=datetime(2026, 11, 9, 16, 0))


def test_an_interval_that_ends_when_it_starts_is_refused() -> None:
    moment = datetime(2026, 11, 9, 15, 0, tzinfo=UTC)

    with pytest.raises(ValueError, match="end after"):
        Interval(start=moment, end=moment)


def test_an_interval_that_ends_before_it_starts_is_refused() -> None:
    with pytest.raises(ValueError, match="end after"):
        Interval(
            start=datetime(2026, 11, 9, 16, 0, tzinfo=UTC),
            end=datetime(2026, 11, 9, 15, 0, tzinfo=UTC),
        )


def test_touching_intervals_do_not_overlap() -> None:
    first = Interval(
        start=datetime(2026, 11, 9, 15, 0, tzinfo=UTC),
        end=datetime(2026, 11, 9, 15, 30, tzinfo=UTC),
    )
    second = Interval(
        start=datetime(2026, 11, 9, 15, 30, tzinfo=UTC),
        end=datetime(2026, 11, 9, 16, 0, tzinfo=UTC),
    )

    assert not first.overlaps(second)
    assert not second.overlaps(first)


def test_intervals_overlap_when_compared_across_utc_offsets() -> None:
    paris = Interval(
        start=datetime(2026, 11, 9, 15, 0, tzinfo=PARIS),
        end=datetime(2026, 11, 9, 15, 30, tzinfo=PARIS),
    )
    bucharest = Interval(
        start=datetime(2026, 11, 9, 16, 0, tzinfo=BUCHAREST),
        end=datetime(2026, 11, 9, 16, 30, tzinfo=BUCHAREST),
    )

    assert paris.overlaps(bucharest)
    assert bucharest.overlaps(paris)


def test_the_same_wall_clock_in_different_offsets_is_not_an_overlap() -> None:
    paris = Interval(
        start=datetime(2026, 11, 9, 15, 0, tzinfo=PARIS),
        end=datetime(2026, 11, 9, 15, 30, tzinfo=PARIS),
    )
    bucharest = Interval(
        start=datetime(2026, 11, 9, 15, 0, tzinfo=BUCHAREST),
        end=datetime(2026, 11, 9, 15, 30, tzinfo=BUCHAREST),
    )

    assert not paris.overlaps(bucharest)
