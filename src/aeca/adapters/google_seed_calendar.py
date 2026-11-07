"""Seeds test events through the Calendar API, which the Calendar port cannot express: recurrence,
all-day events, status and extended properties.

SeedEvent lives here, not under aeca.tools, because nothing outside the tools may import them."""

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Literal

from aeca.adapters.google_calendar import CALENDAR_ID
from aeca.adapters.google_errors import execute


@dataclass(frozen=True)
class SeedEvent:
    summary: str
    start: datetime | date
    end: datetime | date
    zone: str
    private_properties: dict[str, str]
    recurrence: tuple[str, ...] = ()
    status: Literal["confirmed", "tentative"] = "confirmed"
    declined_by_me: bool = False


class GoogleSeedCalendar:
    def __init__(self, service: Any, account_address: str) -> None:
        self._events = service.events()
        self._account_address = account_address

    def insert(self, event: SeedEvent) -> str:
        reply = execute(
            self._events.insert(calendarId=CALENDAR_ID, body=self._body(event), sendUpdates="none")
        )
        return str(reply["id"])

    def _body(self, event: SeedEvent) -> dict[str, Any]:
        body: dict[str, Any] = {
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
