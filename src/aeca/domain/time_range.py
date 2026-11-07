from dataclasses import dataclass
from datetime import UTC, datetime


def in_utc(moment: datetime) -> datetime:
    """Two datetimes in one ZoneInfo compare by wall clock and ignore fold, so compare in UTC."""
    return moment.astimezone(UTC)


@dataclass(frozen=True)
class Interval:
    start: datetime
    end: datetime

    def __post_init__(self) -> None:
        if self.start.utcoffset() is None or self.end.utcoffset() is None:
            raise ValueError("an interval needs timezone-aware datetimes")
        if in_utc(self.end) <= in_utc(self.start):
            raise ValueError("an interval must end after it starts")

    def overlaps(self, other: "Interval") -> bool:
        return in_utc(self.start) < in_utc(other.end) and in_utc(other.start) < in_utc(self.end)
