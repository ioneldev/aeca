"""Google Calendar behind the Calendar port."""

from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from googleapiclient.errors import HttpError

from aeca.adapters.google_errors import classify, execute, execute_unless_missing
from aeca.domain.calendar_event import CalendarEvent, NewEvent
from aeca.domain.time_range import Interval

CALENDAR_ID = "primary"
# The status Google gives an event that was deleted: it stays readable by id.
CANCELLED = "cancelled"
LIST_PAGE_SIZE = 2500
# Day-1 spike: 404 is certain, 410 is how Google may report an event that was deleted for good.
MISSING_EVENT_STATUSES = (404, 410)
# Day-1 spike: Google answers 409 "duplicate" to an insert under an id that exists, live or
# cancelled.
ID_ALREADY_EXISTS = 409
# Google strips the times from a cancelled event. Callers read only `cancelled` from it, so
# the interval sits in the distant past where it can never overlap a real window.
NO_INTERVAL = Interval(datetime(1970, 1, 1, tzinfo=UTC), datetime(1970, 1, 1, 0, 1, tzinfo=UTC))
ZERO_LENGTH_PADDING = timedelta(minutes=1)


class GoogleCalendar:
    def __init__(self, service: Any) -> None:
        self._events = service.events()
        self._settings = service.settings()
        self._zone: str | None = None

    def time_zone(self) -> str:
        if self._zone is None:
            self._zone = str(execute(self._settings.get(setting="timezone"))["value"])
        return self._zone

    def events(self, window: Interval) -> list[CalendarEvent]:
        items: list[dict[str, Any]] = []
        page_token: str | None = None
        while True:
            reply = execute(self._events.list(**self._list_arguments(window, page_token)))
            items.extend(reply.get("items", []))
            page_token = reply.get("nextPageToken")
            if not page_token:
                return [self._to_event(item) for item in items]

    def get_event(self, event_id: str) -> CalendarEvent | None:
        item = execute_unless_missing(
            self._events.get(calendarId=CALENDAR_ID, eventId=event_id),
            missing_statuses=MISSING_EVENT_STATUSES,
        )
        return None if item is None else self._to_event(item)

    def event_by_ical_uid(self, uid: str) -> CalendarEvent | None:
        reply = execute(self._events.list(calendarId=CALENDAR_ID, iCalUID=uid))
        items = reply.get("items", [])
        return self._to_event(items[0]) if items else None

    def insert_event(self, event: NewEvent) -> None:
        zone = self.time_zone()
        body = {
            "id": event.id,
            "summary": event.title,
            "description": event.description,
            "start": _timed(event.interval.start, zone),
            "end": _timed(event.interval.end, zone),
        }
        request = self._events.insert(calendarId=CALENDAR_ID, body=body, sendUpdates="none")
        try:
            request.execute()
        except HttpError as error:
            if error.resp.status != ID_ALREADY_EXISTS:
                raise classify(error) from error
        except Exception as error:
            raise classify(error) from error

    def _list_arguments(self, window: Interval, page_token: str | None) -> dict[str, Any]:
        arguments: dict[str, Any] = {
            "calendarId": CALENDAR_ID,
            "timeMin": window.start.isoformat(),
            "timeMax": window.end.isoformat(),
            "singleEvents": True,
            "showDeleted": False,
            "maxResults": LIST_PAGE_SIZE,
        }
        if page_token:
            arguments["pageToken"] = page_token
        return arguments

    def _to_event(self, item: dict[str, Any]) -> CalendarEvent:
        cancelled = item.get("status") == CANCELLED
        all_day = "date" in item.get("start", {})
        return CalendarEvent(
            id=item["id"],
            interval=self._interval_of(item) if "start" in item else NO_INTERVAL,
            all_day=all_day,
            cancelled=cancelled,
            declined_by_me=_declined_by_me(item),
            ical_uid=item.get("iCalUID"),
        )

    def _interval_of(self, item: dict[str, Any]) -> Interval:
        start = self._moment(item["start"])
        end = self._moment(item["end"])
        # Google accepts an event whose end equals its start; an Interval does not.
        return Interval(start, end if end > start else start + ZERO_LENGTH_PADDING)

    def _moment(self, when: dict[str, str]) -> datetime:
        if "date" in when:
            return datetime.combine(
                date.fromisoformat(when["date"]), time.min, tzinfo=ZoneInfo(self.time_zone())
            )
        return datetime.fromisoformat(when["dateTime"])


def _timed(moment: datetime, zone: str) -> dict[str, str]:
    return {"dateTime": moment.astimezone(ZoneInfo(zone)).isoformat(), "timeZone": zone}


def _declined_by_me(item: dict[str, Any]) -> bool:
    return any(
        attendee.get("self") and attendee.get("responseStatus") == "declined"
        for attendee in item.get("attendees", [])
    )
