"""One copy per inbox: the running service's view of the lock (spec §4.12, item 6).

The lock is a courtesy. Keys derived from Gmail message ids already stop a second copy from
redoing work, so a copy that slept and lost the lock does no harm before it notices.
"""

from dataclasses import dataclass

from aeca.application.ports import Clock, Lease
from aeca.domain.errors import ConfigError, LockLost
from aeca.domain.lease import (
    LeaseOutcome,
    LeaseRequest,
    LeaseResult,
    stale_after,
)


@dataclass(frozen=True)
class CopyLockDeps:
    lease: Lease
    clock: Clock
    # Random per process start: it names a running process, not an email.
    copy_id: str
    poll_seconds: float
    force: bool


class CopyLock:
    def __init__(self, deps: CopyLockDeps) -> None:
        self._deps = deps

    def acquire(self) -> None:
        """Raises ConfigError, sending nothing, while another copy's heartbeat is fresh."""
        request = LeaseRequest(
            copy_id=self._deps.copy_id,
            now=self._deps.clock.now(),
            stale_after=stale_after(self._deps.poll_seconds),
            force=self._deps.force,
        )
        result = self._deps.lease.acquire(request)
        if result.outcome is LeaseOutcome.HELD_BY_ANOTHER:
            raise _held_by_another(result, request)

    def ensure_held(self) -> None:
        if not self._deps.lease.still_held(self._deps.copy_id):
            raise LockLost.for_a_lost_lock()

    def renew(self) -> None:
        self._deps.lease.renew(self._deps.copy_id, self._deps.clock.now())


def _held_by_another(result: LeaseResult, request: LeaseRequest) -> ConfigError:
    limit_seconds = int(request.stale_after.total_seconds())
    age = ""
    if result.holding is not None:
        seconds_old = int((request.now - result.holding.heartbeat).total_seconds())
        age = f" Its heartbeat is {seconds_old} s old."
    return ConfigError(
        f"Another copy of AECA holds the inbox lock.{age}",
        next_action="Stop the other copy; or wait until its heartbeat is older than "
        f"{limit_seconds} seconds; or set FORCE_LOCK=true to take the lock",
    )
