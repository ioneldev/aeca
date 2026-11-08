"""A clock that says one fixed time, so no test reads real time."""

from datetime import UTC, datetime

FIXED_NOW = datetime(2026, 11, 7, 12, 0, tzinfo=UTC)


class FixedClock:
    def __init__(self, now: datetime = FIXED_NOW) -> None:
        self._now = now

    def now(self) -> datetime:
        return self._now
