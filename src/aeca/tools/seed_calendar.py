"""Fills one test week in the real calendar. The week is the one the corpus's meeting emails were
written against: each case says whether its time is free, clashes, or has every slot taken."""

import base64
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Literal, Protocol
from zoneinfo import ZoneInfo

from aeca.adapters.google_seed_calendar import SeedEvent, SeedInsert

EVENT_PREFIX = "[AECA test]"
TEST_TAG = {"aeca_test": "true"}
SEED_ID_PREFIX = "aecaseed"
WORKING_DAYS = 5
MONDAY, TUESDAY, WEDNESDAY, THURSDAY, FRIDAY = range(5)


class SeedCalendar(Protocol):
    def insert(self, event: SeedEvent) -> SeedInsert:
        """created is False when an event with this id was already there."""
        ...


@dataclass(frozen=True)
class SeedWeek:
    week_start: date
    zone: ZoneInfo


def next_monday(now: datetime, zone: ZoneInfo) -> date:
    """The first Monday after today, today read in the calendar's zone."""
    today = now.astimezone(zone).date()
    return today + timedelta(days=7 - today.weekday())


@dataclass(frozen=True)
class SeedReport:
    created: list[str]
    skipped: list[str]


def seed_calendar(calendar: SeedCalendar, week: SeedWeek) -> SeedReport:
    inserts = [calendar.insert(event) for event in _events_of(week)]
    return SeedReport(
        created=[insert.event_id for insert in inserts if insert.created],
        skipped=[insert.event_id for insert in inserts if not insert.created],
    )


def _events_of(week: SeedWeek) -> list[SeedEvent]:
    return [
        *(_focus_block(week, day) for day in range(WORKING_DAYS)),
        _weekly_sync(week),
        _offsite(week),
        *_booked_thursday(week),
        _tentative_vendor_call(week),
        _declined_meeting(week),
    ]


@dataclass(frozen=True)
class _Slot:
    """What identifies an event within the week: its key makes its id, and so a rerun finds it."""

    key: str
    summary: str
    recurrence: tuple[str, ...] = ()
    status: Literal["confirmed", "tentative"] = "confirmed"
    declined_by_me: bool = False


def _focus_block(week: SeedWeek, day: int) -> SeedEvent:
    return _timed(week, _Slot(f"focus-{day}", "Focus block"), day=day, hours=(10, 11))


def _weekly_sync(week: SeedWeek) -> SeedEvent:
    slot = _Slot("weekly-sync", "Weekly sync", recurrence=("RRULE:FREQ=WEEKLY;COUNT=4",))
    return _timed(week, slot, day=TUESDAY, hours=(14, 15))


def _offsite(week: SeedWeek) -> SeedEvent:
    wednesday = week.week_start + timedelta(days=WEDNESDAY)
    return _event(
        week, _Slot("offsite", "Offsite"), start=wednesday, end=wednesday + timedelta(days=1)
    )


def _booked_thursday(week: SeedWeek) -> list[SeedEvent]:
    return [
        _timed(
            week,
            _Slot(f"booked-{hour:02d}", f"Booked {hour:02d}:00"),
            day=THURSDAY,
            hours=(hour, hour + 1),
        )
        for hour in range(9, 17)
    ]


def _tentative_vendor_call(week: SeedWeek) -> SeedEvent:
    slot = _Slot("vendor-call", "Vendor call (tentative)", status="tentative")
    return _timed(week, slot, day=FRIDAY, hours=(15, 16))


def _declined_meeting(week: SeedWeek) -> SeedEvent:
    slot = _Slot("declined", "Declined meeting", declined_by_me=True)
    return _timed(week, slot, day=MONDAY, hours=(13, 14))


def _timed(week: SeedWeek, slot: _Slot, *, day: int, hours: tuple[int, int]) -> SeedEvent:
    date_of_day = week.week_start + timedelta(days=day)
    start_hour, end_hour = hours
    return _event(
        week,
        slot,
        start=datetime.combine(date_of_day, time(start_hour), tzinfo=week.zone),
        end=datetime.combine(date_of_day, time(end_hour), tzinfo=week.zone),
    )


def _event(
    week: SeedWeek, slot: _Slot, *, start: datetime | date, end: datetime | date
) -> SeedEvent:
    return SeedEvent(
        summary=f"{EVENT_PREFIX} {slot.summary}",
        start=start,
        end=end,
        zone=week.zone.key,
        private_properties=dict(TEST_TAG),
        event_id=_event_id(week.week_start, slot.key),
        recurrence=slot.recurrence,
        status=slot.status,
        declined_by_me=slot.declined_by_me,
    )


def _event_id(week_start: date, slot_key: str) -> str:
    """Google accepts ids of base32hex characters, 0-9 and a-v, five to 1024 of them."""
    encoded = base64.b32hexencode(f"{week_start.isoformat()}:{slot_key}".encode()).decode()
    return SEED_ID_PREFIX + encoded.rstrip("=").lower()
