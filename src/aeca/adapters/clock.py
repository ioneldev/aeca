from datetime import UTC, datetime
from typing import Protocol


class NowProvider(Protocol):
    def now(self) -> datetime: ...


class SystemClock:
    """The only place in AECA that reads the real time."""

    def now(self) -> datetime:
        return datetime.now(UTC)
