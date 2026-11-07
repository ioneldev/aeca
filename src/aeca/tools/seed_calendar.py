"""Fills one test week in the real calendar. The week is the one the corpus's meeting emails were
written against: each case says whether its time is free, clashes, or has every slot taken."""

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Literal, Protocol
from zoneinfo import ZoneInfo

from aeca.adapters.google_seed_calendar import SeedEvent

EVENT_PREFIX = "[AECA test]"
TEST_TAG = {"aeca_test": "true"}
WORKING_DAYS = 5
MONDAY, TUESDAY, WEDNESDAY, THURSDAY, FRIDAY = range(5)


class SeedCalendar(Protocol):
    def insert(self, event: SeedEvent) -> str:
        """The created event's id."""
        ...


@dataclass(frozen=True)
class SeedWeek:
    week_start: date
    zone: ZoneInfo


def next_monday(now: datetime, zone: ZoneInfo) -> date:
    """The first Monday after today, today read in the calendar's zone."""
    today = now.astimezone(zone).date()
    return today + timedelta(days=7 - today.weekday())


def seed_calendar(calendar: SeedCalendar, week: SeedWeek) -> list[str]:
    return [calendar.insert(event) for event in _events_of(week)]


def _events_of(week: SeedWeek) -> list[SeedEvent]:
    return [
        *(_focus_block(week, day) for day in range(WORKING_DAYS)),
        _weekly_sync(week),
        _offsite(week),
        *_booked_thursday(week),
        _tentative_vendor_call(week),
        _declined_meeting(week),
    ]


def _focus_block(week: SeedWeek, day: int) -> SeedEvent:
    return _timed(week, day, "Focus block", hours=(10, 11))


def _weekly_sync(week: SeedWeek) -> SeedEvent:
    return _timed(
        week, TUESDAY, "Weekly sync", hours=(14, 15), recurrence=("RRULE:FREQ=WEEKLY;COUNT=4",)
    )


def _offsite(week: SeedWeek) -> SeedEvent:
    wednesday = week.week_start + timedelta(days=WEDNESDAY)
    return _event(
        week,
        "Offsite",
        start=wednesday,
        end=wednesday + timedelta(days=1),
    )


def _booked_thursday(week: SeedWeek) -> list[SeedEvent]:
    return [
        _timed(week, THURSDAY, f"Booked {hour:02d}:00", hours=(hour, hour + 1))
        for hour in range(9, 17)
    ]


def _tentative_vendor_call(week: SeedWeek) -> SeedEvent:
    return _timed(week, FRIDAY, "Vendor call (tentative)", hours=(15, 16), status="tentative")


def _declined_meeting(week: SeedWeek) -> SeedEvent:
    return _timed(week, MONDAY, "Declined meeting", hours=(13, 14), declined_by_me=True)


def _timed(
    week: SeedWeek,
    day: int,
    summary: str,
    *,
    hours: tuple[int, int],
    recurrence: tuple[str, ...] = (),
    status: Literal["confirmed", "tentative"] = "confirmed",
    declined_by_me: bool = False,
) -> SeedEvent:
    date_of_day = week.week_start + timedelta(days=day)
    start_hour, end_hour = hours
    return _event(
        week,
        summary,
        start=datetime.combine(date_of_day, time(start_hour), tzinfo=week.zone),
        end=datetime.combine(date_of_day, time(end_hour), tzinfo=week.zone),
        recurrence=recurrence,
        status=status,
        declined_by_me=declined_by_me,
    )


def _event(
    week: SeedWeek,
    summary: str,
    *,
    start: datetime | date,
    end: datetime | date,
    recurrence: tuple[str, ...] = (),
    status: Literal["confirmed", "tentative"] = "confirmed",
    declined_by_me: bool = False,
) -> SeedEvent:
    return SeedEvent(
        summary=f"{EVENT_PREFIX} {summary}",
        start=start,
        end=end,
        zone=week.zone.key,
        private_properties=dict(TEST_TAG),
        recurrence=recurrence,
        status=status,
        declined_by_me=declined_by_me,
    )
