from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class Interval:
    start: datetime
    end: datetime

    def __post_init__(self) -> None:
        if self.start.utcoffset() is None or self.end.utcoffset() is None:
            raise ValueError("an interval needs timezone-aware datetimes")
        if self.end <= self.start:
            raise ValueError("an interval must end after it starts")

    def overlaps(self, other: "Interval") -> bool:
        return self.start < other.end and other.start < self.end
