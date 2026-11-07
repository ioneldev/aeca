"""When reset-test must not run: while any copy of AECA may hold the inbox lock.

AECA's own lease rule (domain/lease.py) is right for a copy deciding whether to start, and fails
open three ways that are wrong for a tool that trashes mail: an unreadable lock counts as free,
FORCE_LOCK takes it, and a heartbeat far in the future counts as stale. This rule fails closed.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta

from aeca.domain.lease import stale_after

# The lock body records no poll interval, and an evaluator's copy may poll far less often than this
# checkout's POLL_SECONDS says, so the window never drops below ten minutes (ruling P4).
MINIMUM_LOCK_WINDOW = timedelta(minutes=10)


def reset_lock_window(poll_seconds: float) -> timedelta:
    return max(stale_after(poll_seconds), MINIMUM_LOCK_WINDOW)


@dataclass(frozen=True, slots=True)
class LockReading:
    draft_id: str
    # None when the draft holds no heartbeat with a timezone.
    heartbeat: datetime | None


@dataclass(frozen=True, slots=True)
class LockCheck:
    now: datetime
    window: timedelta


def blocking_lock(readings: Iterable[LockReading], check: LockCheck) -> LockReading | None:
    """The first lock draft that may belong to a running copy, None when every one is stale."""
    return next((reading for reading in readings if _may_be_held(reading, check)), None)


# A heartbeat from the future means a clock is wrong somewhere, which is no proof that the copy
# writing it has stopped.
def _may_be_held(reading: LockReading, check: LockCheck) -> bool:
    if reading.heartbeat is None:
        return True
    return check.now - reading.heartbeat < check.window
