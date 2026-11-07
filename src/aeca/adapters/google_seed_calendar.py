"""Seeds test events through the Calendar API, which the Calendar port cannot express: recurrence,
all-day events, status and extended properties.

SeedEvent lives here, not under aeca.tools, because no domain, application or adapter code may
import the tools."""

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Literal

from aeca.adapters.google_calendar import CALENDAR_ID
from aeca.adapters.google_errors import execute_unless_missing


@dataclass(frozen=True)
class SeedEvent:
    summary: str
    start: datetime | date
    end: datetime | date
    zone: str
    private_properties: dict[str, str]
    # Google refuses an id it already holds, which is what makes seeding twice harmless.
    event_id: str
    recurrence: tuple[str, ...] = ()
    status: Literal["confirmed", "tentative"] = "confirmed"
    declined_by_me: bool = False


@dataclass(frozen=True)
class SeedInsert:
    event_id: str
    created: bool


ALREADY_EXISTS = 409


class GoogleSeedCalendar:
    def __init__(self, service: Any, account_address: str) -> None:
        self._events = service.events()
        self._account_address = account_address

    def insert(self, event: SeedEvent) -> SeedInsert:
        reply = execute_unless_missing(
            self._events.insert(calendarId=CALENDAR_ID, body=self._body(event), sendUpdates="none"),
            missing_statuses=(ALREADY_EXISTS,),
        )
        return SeedInsert(event_id=event.event_id, created=reply is not None)

    def _body(self, event: SeedEvent) -> dict[str, Any]:
        body: dict[str, Any] = {
            "id": event.event_id,
            "summary": event.summary,
            "start": _moment(event.start, event.zone),
            "end": _moment(event.end, event.zone),
            "status": event.status,
            "extendedProperties": {"private": dict(event.private_properties)},
        }
        if event.recurrence:
            body["recurrence"] = list(event.recurrence)
        if event.declined_by_me:
            body["attendees"] = [self._declined_attendee()]
        return body

    def _declined_attendee(self) -> dict[str, Any]:
        return {"email": self._account_address, "self": True, "responseStatus": "declined"}


def _moment(moment: datetime | date, zone: str) -> dict[str, str]:
    if isinstance(moment, datetime):
        return {"dateTime": moment.isoformat(), "timeZone": zone}
    return {"date": moment.isoformat()}
