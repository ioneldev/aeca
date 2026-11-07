from dataclasses import dataclass

from aeca.domain.time_range import Interval


@dataclass(frozen=True)
class CalendarEvent:
    id: str
    interval: Interval
    all_day: bool
    cancelled: bool
    declined_by_me: bool
    ical_uid: str | None


@dataclass(frozen=True)
class NewEvent:
    id: str
    title: str
    description: str
    interval: Interval
