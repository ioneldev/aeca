from datetime import UTC, datetime


class SystemClock:
    """The only place in AECA that reads the real time."""

    def now(self) -> datetime:
        return datetime.now(UTC)
