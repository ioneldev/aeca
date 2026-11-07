"""One copy per inbox: the running service's view of the lock (spec §4.12, item 6).

The lock is a courtesy. Keys derived from Gmail message ids already stop a second copy from
redoing work, so a copy that slept and lost the lock does no harm before it notices.
"""

from dataclasses import dataclass
from datetime import timedelta

from aeca.application.ports import Clock, Lease
from aeca.domain.errors import ConfigError, EmailError, LockLost, SystemWideError
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
    # Kept in the data volume, so it names one installation, not a process and not an email.
    copy_id: str
    poll_seconds: float
    force: bool


class CopyLock:
    def __init__(self, deps: CopyLockDeps) -> None:
        self._deps = deps
        self._last_renewal = deps.clock.now()

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
        self._last_renewal = request.now

    def ensure_held(self) -> None:
        """Raises LockLost; any other failure is a system-wide error, to back off from."""
        try:
            held = self._deps.lease.still_held(self._deps.copy_id)
        except EmailError as error:
            raise _system_wide(error) from error
        if not held:
            raise LockLost.for_a_lost_lock()

    def renew(self) -> None:
        now = self._deps.clock.now()
        try:
            self._deps.lease.renew(self._deps.copy_id, now)
        except EmailError as error:
            raise _system_wide(error) from error
        self._last_renewal = now

    def renew_if_due(self) -> bool:
        """Renews once a poll has passed since the last renewal; True when it did."""
        if self._deps.clock.now() - self._last_renewal < timedelta(seconds=self._deps.poll_seconds):
            return False
        self.renew()
        return True


# A failure of the lease's own Gmail calls is about no email, so the whole tick backs off.
def _system_wide(error: EmailError) -> SystemWideError:
    return SystemWideError(str(error), next_action=error.next_action)


def _held_by_another(result: LeaseResult, request: LeaseRequest) -> ConfigError:
    limit_seconds = int(request.stale_after.total_seconds())
    age = ""
    if result.holding is not None:
        seconds_old = max(0, int((request.now - result.holding.heartbeat).total_seconds()))
        age = f" Its heartbeat is {seconds_old} s old."
    return ConfigError(
        f"Another copy of AECA holds the inbox lock.{age}",
        next_action="Stop the other copy; or wait until its heartbeat is older than "
        f"{limit_seconds} seconds; or set FORCE_LOCK=true to take the lock",
    )
