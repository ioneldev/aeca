"""Plans a meeting around the calendar: a free proposed slot, else working-hours alternatives."""

import re
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from aeca.domain.approval import Hold
from aeca.domain.calendar_event import CalendarEvent
from aeca.domain.decision import Meeting, Window
from aeca.domain.meeting_outcome import SlotFree
from aeca.domain.time_range import Interval, in_utc

MAX_ALTERNATIVES = 3
ALTERNATIVE_STEP_MINUTES = 30
DAYS_IN_A_WEEK = 7
DAY_NUMBERS = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}
CLOCK_TIME = re.compile(r"\d{2}:\d{2}")


@dataclass(frozen=True, slots=True)
class WorkHours:
    days: frozenset[int]
    start: time
    end: time

    def __post_init__(self) -> None:
        if not self.days:
            raise ValueError("work hours need at least one day")
        if self.end <= self.start:
            raise ValueError("work hours must end after they start")


@dataclass(frozen=True, slots=True)
class PlanSettings:
    zone: ZoneInfo
    work_hours: WorkHours
    meeting_minutes: int
    buffer_minutes: int
    alternative_days: int

    def __post_init__(self) -> None:
        if self.alternative_days < 1:
            raise ValueError("alternative_days must be at least 1")
        if self.meeting_minutes < 1:
            raise ValueError("meeting_minutes must be at least 1")
        if self.buffer_minutes < 0:
            raise ValueError("buffer_minutes must be 0 or more")


@dataclass(frozen=True, slots=True)
class SlotRequest:
    windows: tuple[Window, ...]
    busy: tuple[Interval, ...]
    duration: timedelta
    settings: PlanSettings


@dataclass(frozen=True, slots=True)
class AlternativesRequest:
    now: datetime
    busy: tuple[Interval, ...]
    duration: timedelta
    settings: PlanSettings


def parse_work_hours(text: str) -> WorkHours:
    try:
        days_text, hours_text = text.split()
        start_text, end_text = hours_text.split("-")
        return WorkHours(
            days=_parse_days(days_text),
            start=_parse_clock_time(start_text),
            end=_parse_clock_time(end_text),
        )
    except ValueError as problem:
        raise ValueError(f"cannot read work hours {text!r}: {problem}") from problem


def meeting_duration(meeting: Meeting, settings: PlanSettings) -> timedelta:
    return timedelta(minutes=meeting.duration_minutes or settings.meeting_minutes)


def busy_intervals(
    events: Sequence[CalendarEvent], holds: Sequence[Hold], thread_id: str
) -> tuple[Interval, ...]:
    from_events = (
        event.interval for event in events if not (event.cancelled or event.declined_by_me)
    )
    from_other_threads = (hold.interval for hold in holds if hold.thread_id != thread_id)
    return (*from_events, *from_other_threads)


def choose_slot(request: SlotRequest) -> SlotFree | None:
    settings = request.settings
    for window in request.windows:
        slot = _first_free_slot_in(window, request)
        if slot is not None:
            return SlotFree(slot=slot, outside_work_hours=not _inside_work_hours(slot, settings))
    return None


def find_alternatives(request: AlternativesRequest) -> tuple[Interval, ...]:
    found: list[Interval] = []
    for day in _days_to_search(request.now, request.settings):
        slot = _earliest_free_slot_on(day, request)
        if slot is not None:
            found.append(slot)
        if len(found) == MAX_ALTERNATIVES:
            break
    return tuple(found)


def alternatives_search_span(now: datetime, settings: PlanSettings) -> Interval:
    """From now to the closing time of the last working day find_alternatives looks at."""
    last_day = max(_days_to_search(now, settings))
    _, closing = _work_day_boundaries(last_day, settings)
    return Interval(start=now, end=closing)


def _parse_days(text: str) -> frozenset[int]:
    days: set[int] = set()
    for item in text.split(","):
        first, _, last = item.partition("-")
        first_day = _parse_day(first)
        last_day = _parse_day(last) if "-" in item else first_day
        if last_day < first_day:
            raise ValueError(f"the day range {item!r} runs backwards")
        days.update(range(first_day, last_day + 1))
    return frozenset(days)


def _parse_day(name: str) -> int:
    try:
        return DAY_NUMBERS[name.lower()]
    except KeyError:
        raise ValueError(f"{name!r} is not a day name") from None


def _parse_clock_time(text: str) -> time:
    if not CLOCK_TIME.fullmatch(text):
        raise ValueError(f"{text!r} is not a HH:MM time")
    return time.fromisoformat(text)


def _with_buffer(busy: Sequence[Interval], buffer_minutes: int) -> tuple[Interval, ...]:
    buffer = timedelta(minutes=buffer_minutes)
    return tuple(
        Interval(
            start=(in_utc(span.start) - buffer),
            end=(in_utc(span.end) + buffer),
        )
        for span in busy
    )


def _first_free_slot_in(window: Window, request: SlotRequest) -> Interval | None:
    blocked = _with_buffer(request.busy, request.settings.buffer_minutes)
    for start in _candidate_starts(window, blocked):
        slot = _slot_starting_at(start, request.duration, request.settings.zone)
        if _fits_in_window(slot, window) and not _collides(slot, blocked):
            return slot
    return None


def _candidate_starts(window: Window, blocked: Sequence[Interval]) -> list[datetime]:
    if window.end is None:
        return [window.start]
    ends_inside_window = [
        span.end for span in blocked if in_utc(window.start) < span.end < in_utc(window.end)
    ]
    return sorted([window.start, *ends_inside_window], key=in_utc)


def _slot_starting_at(start: datetime, duration: timedelta, zone: ZoneInfo) -> Interval:
    # Added in UTC so the length is real elapsed time, whatever the zone's clock does meanwhile.
    end = start.astimezone(UTC) + duration
    return Interval(start=start.astimezone(zone), end=end.astimezone(zone))


def _fits_in_window(slot: Interval, window: Window) -> bool:
    return window.end is None or in_utc(slot.end) <= in_utc(window.end)


def _collides(slot: Interval, blocked: Sequence[Interval]) -> bool:
    return any(slot.overlaps(span) for span in blocked)


def _inside_work_hours(slot: Interval, settings: PlanSettings) -> bool:
    day = slot.start.astimezone(settings.zone).date()
    if day.weekday() not in settings.work_hours.days:
        return False
    opening, closing = _work_day_boundaries(day, settings)
    return in_utc(opening) <= in_utc(slot.start) and in_utc(slot.end) <= in_utc(closing)


def _work_day_boundaries(day: date, settings: PlanSettings) -> tuple[datetime, datetime]:
    hours = settings.work_hours
    zone = settings.zone
    opening = datetime(
        day.year, day.month, day.day, hours.start.hour, hours.start.minute, tzinfo=zone
    )
    closing = datetime(day.year, day.month, day.day, hours.end.hour, hours.end.minute, tzinfo=zone)
    return opening, closing


def _days_to_search(now: datetime, settings: PlanSettings) -> Iterator[date]:
    today = now.astimezone(settings.zone).date()
    offered = 0
    for offset in range(settings.alternative_days * DAYS_IN_A_WEEK + 1):
        day = today + timedelta(days=offset)
        if _has_working_time_left(day, now=now, settings=settings):
            yield day
            offered += 1
            if offered == settings.alternative_days:
                return


def _has_working_time_left(day: date, *, now: datetime, settings: PlanSettings) -> bool:
    if day.weekday() not in settings.work_hours.days:
        return False
    _, closing = _work_day_boundaries(day, settings)
    return in_utc(now) < in_utc(closing)


def _earliest_free_slot_on(day: date, request: AlternativesRequest) -> Interval | None:
    settings = request.settings
    blocked = _with_buffer(request.busy, settings.buffer_minutes)
    opening, closing = _work_day_boundaries(day, settings)
    for start in _half_hour_starts(day, opening, closing):
        if in_utc(start) <= in_utc(request.now):
            continue
        slot = _slot_starting_at(start, request.duration, settings.zone)
        if in_utc(slot.end) <= in_utc(closing) and not _collides(slot, blocked):
            return slot
    return None


def _half_hour_starts(day: date, opening: datetime, closing: datetime) -> Iterator[datetime]:
    zone = opening.tzinfo
    first_minute = _round_up(opening.hour * 60 + opening.minute, ALTERNATIVE_STEP_MINUTES)
    for minute_of_day in range(
        first_minute, closing.hour * 60 + closing.minute, ALTERNATIVE_STEP_MINUTES
    ):
        hour, minute = divmod(minute_of_day, 60)
        yield datetime(day.year, day.month, day.day, hour, minute, tzinfo=zone)


def _round_up(value: int, step: int) -> int:
    return -(-value // step) * step
