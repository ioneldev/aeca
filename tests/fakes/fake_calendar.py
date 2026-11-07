"""A calendar held in memory, which records every read so a test can check what was asked.

By default it answers as the Google adapter does (day-1 spike 10): an insert under an id that
already exists, live or cancelled, changes nothing. A test can make it misbehave instead.
"""

from fakes.crash import Killed

from aeca.domain.calendar_event import CalendarEvent, NewEvent
from aeca.domain.time_range import Interval

BUCHAREST = "Europe/Bucharest"


class FakeCalendar:
    def __init__(self, zone: str = BUCHAREST, *, journal: list[str] | None = None) -> None:
        self._zone = zone
        self._events: list[CalendarEvent] = []
        # Shared with other fakes, so a test can assert the order across them.
        self._journal = journal if journal is not None else []
        self.event_reads: list[Interval] = []
        self.uid_reads: list[str] = []
        self.inserted: list[NewEvent] = []
        # Google refuses a second event under one id; Google's docs never promise it, so a test can
        # take the promise away.
        self.accepts_duplicate_ids = False
        # The insert answers success, and no event is there afterwards.
        self.inserts_vanish = False
        # Every inserted event reads back cancelled, as if deleted the moment it was made.
        self.inserts_come_back_cancelled = False
        # Failures raised by the next calls to insert_event(), in order, before anything is made.
        self.insert_failures: list[Exception] = []
        # Raise Killed right after the next insert.
        self.kill_after_insert = False

    def add(self, event: CalendarEvent) -> None:
        self._events = [known for known in self._events if known.id != event.id]
        self._events.append(event)

    def events_with_id(self, event_id: str) -> list[CalendarEvent]:
        return [event for event in self._events if event.id == event_id]

    def time_zone(self) -> str:
        return self._zone

    def events(self, window: Interval) -> list[CalendarEvent]:
        self.event_reads.append(window)
        return [
            event
            for event in self._events
            if not event.cancelled and event.interval.overlaps(window)
        ]

    def get_event(self, event_id: str) -> CalendarEvent | None:
        return next((event for event in self._events if event.id == event_id), None)

    def event_by_ical_uid(self, uid: str) -> CalendarEvent | None:
        self.uid_reads.append(uid)
        return next((event for event in self._events if event.ical_uid == uid), None)

    def insert_event(self, event: NewEvent) -> None:
        if self.insert_failures:
            raise self.insert_failures.pop(0)
        self.inserted.append(event)
        self._journal.append("insert_event")
        if self._keeps(event):
            self._events.append(_stored(event, cancelled=self.inserts_come_back_cancelled))
        if self.kill_after_insert:
            self.kill_after_insert = False
            raise Killed

    def _keeps(self, event: NewEvent) -> bool:
        if self.inserts_vanish:
            return False
        return self.accepts_duplicate_ids or self.get_event(event.id) is None


def _stored(event: NewEvent, *, cancelled: bool) -> CalendarEvent:
    return CalendarEvent(
        id=event.id,
        interval=event.interval,
        all_day=False,
        cancelled=cancelled,
        declined_by_me=False,
        ical_uid=None,
    )
