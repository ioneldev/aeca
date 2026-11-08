from datetime import UTC, datetime, timedelta

from aeca.domain.lease import (
    LeaseHolding,
    LeaseOutcome,
    LeaseRequest,
    decide_acquisition,
    is_fresh,
)

NOW = datetime(2026, 11, 7, 12, 0, tzinfo=UTC)
STALE_AFTER = timedelta(seconds=90)


def request() -> LeaseRequest:
    return LeaseRequest(copy_id="mine", now=NOW, stale_after=STALE_AFTER, force=False)


def test_a_heartbeat_slightly_ahead_is_fresh_clock_skew() -> None:
    holding = LeaseHolding("theirs", NOW + timedelta(seconds=30))

    assert decide_acquisition(holding, request()) is LeaseOutcome.HELD_BY_ANOTHER


def test_a_heartbeat_more_than_the_stale_limit_ahead_counts_as_stale() -> None:
    holding = LeaseHolding("theirs", NOW + STALE_AFTER + timedelta(seconds=1))

    assert decide_acquisition(holding, request()) is LeaseOutcome.TOOK_OVER_STALE


def test_freshness_is_symmetric_around_now() -> None:
    assert is_fresh(NOW - timedelta(seconds=89), NOW, STALE_AFTER)
    assert not is_fresh(NOW - STALE_AFTER, NOW, STALE_AFTER)
    assert is_fresh(NOW + STALE_AFTER, NOW, STALE_AFTER)
    assert not is_fresh(NOW + STALE_AFTER + timedelta(seconds=1), NOW, STALE_AFTER)
