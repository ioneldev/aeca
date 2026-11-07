"""A calendar held in memory, which records every read so a test can check what was asked."""

from aeca.domain.calendar_event import CalendarEvent, NewEvent
from aeca.domain.time_range import Interval

BUCHAREST = "Europe/Bucharest"


class FakeCalendar:
    def __init__(self, zone: str = BUCHAREST) -> None:
        self._zone = zone
        self._events: dict[str, CalendarEvent] = {}
        self.event_reads: list[Interval] = []
        self.uid_reads: list[str] = []
        self.inserted: list[NewEvent] = []

    def add(self, event: CalendarEvent) -> None:
        self._events[event.id] = event

    def time_zone(self) -> str:
        return self._zone

    def events(self, window: Interval) -> list[CalendarEvent]:
        self.event_reads.append(window)
        return [
            event
            for event in self._events.values()
            if not event.cancelled and event.interval.overlaps(window)
        ]

    def get_event(self, event_id: str) -> CalendarEvent | None:
        return self._events.get(event_id)

    def event_by_ical_uid(self, uid: str) -> CalendarEvent | None:
        self.uid_reads.append(uid)
        return next((event for event in self._events.values() if event.ical_uid == uid), None)

    def insert_event(self, event: NewEvent) -> None:
        self.inserted.append(event)
        self._events[event.id] = CalendarEvent(
            id=event.id,
            interval=event.interval,
            all_day=False,
            cancelled=False,
            declined_by_me=False,
            ical_uid=None,
        )
