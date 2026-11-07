from dataclasses import dataclass, field

from aeca.domain.time_range import Interval


@dataclass(frozen=True)
class CalendarEvent:
    id: str
    interval: Interval
    all_day: bool
    cancelled: bool
    declined_by_me: bool
    ical_uid: str | None


@dataclass(frozen=True, slots=True)
class CalendarEntry:
    """An event as reset-test lists it: what its plan shows, and what its event rules check."""

    # The id to cancel: a recurring event's master, never one of its instances.
    id: str
    summary: str
    # The start as the calendar gives it: a date, or a date and time.
    starts: str
    cancelled: bool
    private_properties: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class NewEvent:
    id: str
    title: str
    description: str
    interval: Interval
