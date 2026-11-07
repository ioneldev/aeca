"""The rule for who holds an inbox, whatever stores the lock."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

# One value for both the lock and the health check, so they can never disagree on "stale".
POLLS_BEFORE_A_HEARTBEAT_IS_STALE = 3


def stale_after(poll_seconds: float) -> timedelta:
    return timedelta(seconds=POLLS_BEFORE_A_HEARTBEAT_IS_STALE * poll_seconds)


@dataclass(frozen=True, slots=True)
class LeaseHolding:
    copy_id: str
    heartbeat: datetime


@dataclass(frozen=True, slots=True)
class LeaseRequest:
    copy_id: str
    now: datetime
    stale_after: timedelta
    force: bool


class LeaseOutcome(StrEnum):
    ACQUIRED = "acquired"
    TOOK_OVER_STALE = "took_over_stale"
    FORCED = "forced"
    HELD_BY_ANOTHER = "held_by_another"


@dataclass(frozen=True, slots=True)
class LeaseResult:
    outcome: LeaseOutcome
    # What the lock said before this request, so a refusal can name how fresh it is.
    holding: LeaseHolding | None


def is_fresh(heartbeat: datetime, now: datetime, stale_after: timedelta) -> bool:
    """A heartbeat from the future beyond the stale limit is clock skew gone wrong, not a live
    copy: it would otherwise hold the lock, or the health check, for ever."""
    return -stale_after <= now - heartbeat < stale_after


def decide_acquisition(holding: LeaseHolding | None, request: LeaseRequest) -> LeaseOutcome:
    if holding is None or holding.copy_id == request.copy_id:
        return LeaseOutcome.ACQUIRED
    if request.force:
        return LeaseOutcome.FORCED
    if is_fresh(holding.heartbeat, request.now, request.stale_after):
        return LeaseOutcome.HELD_BY_ANOTHER
    return LeaseOutcome.TOOK_OVER_STALE
