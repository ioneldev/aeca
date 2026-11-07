"""What reset-test reads from Gmail and the calendar, and its only two writes.

It writes with messages.trash, on one message at a time, and events.delete, which Google keeps as a
cancelled event (measured 2026-11-07). It never calls threads, labels, filters, settings, or any
draft write, so neither the lock nor anything else can be changed through it.
"""

import base64
from collections.abc import Callable, Iterator
from datetime import datetime
from email import message_from_bytes, policy
from typing import Any

from aeca.adapters.gmail import raw_email_from_reply
from aeca.adapters.gmail_lease import LOCK_SUBJECT, lock_holding_of
from aeca.adapters.google_calendar import CALENDAR_ID, CANCELLED, MISSING_EVENT_STATUSES
from aeca.adapters.google_errors import execute, execute_unless_missing
from aeca.domain.calendar_event import CalendarEntry
from aeca.domain.email import RawEmail
from aeca.domain.lease import LOCK_KIND
from aeca.domain.reset_lock import LockReading

USER = "me"
MESSAGE_PAGE_SIZE = 500
EVENT_PAGE_SIZE = 2500


class GmailResetMailbox:
    def __init__(self, service: Any) -> None:
        self._messages = service.users().messages()
        self._drafts = service.users().drafts()
        self._labels = service.users().labels()

    def every_message(self) -> Iterator[RawEmail]:
        """Spam, Trash and drafts included: a source already in Trash still has derived work to
        find, and the plan must see a message in Spam."""
        for entry in _every_item(self._message_page, "messages"):
            reply = execute_unless_missing(
                self._messages.get(userId=USER, id=entry["id"], format="raw")
            )
            if reply is not None:
                yield raw_email_from_reply(str(entry["id"]), reply)

    def label_ids_by_name(self) -> dict[str, str]:
        listed = execute(self._labels.list(userId=USER))
        return {str(label["name"]): str(label["id"]) for label in listed.get("labels", [])}

    def lock_readings(self) -> list[LockReading]:
        """Every lock draft, not only the lowest one AECA's own lease reads."""
        readings = []
        for entry in _every_item(self._draft_page, "drafts"):
            reply = execute_unless_missing(
                self._drafts.get(userId=USER, id=entry["id"], format="raw")
            )
            if reply is not None and _may_be_a_lock(reply):
                readings.append(LockReading(str(entry["id"]), _heartbeat_in(reply)))
        return readings

    def trash(self, message_id: str) -> None:
        execute(self._messages.trash(userId=USER, id=message_id))

    def _message_page(self, page_token: str | None) -> Any:
        return self._messages.list(
            userId=USER,
            includeSpamTrash=True,
            maxResults=MESSAGE_PAGE_SIZE,
            **_page(page_token),
        )

    def _draft_page(self, page_token: str | None) -> Any:
        return self._drafts.list(userId=USER, **_page(page_token))


class GoogleResetCalendar:
    def __init__(self, service: Any) -> None:
        self._events = service.events()

    def tagged_events(self, name: str, value: str) -> list[CalendarEntry]:
        """Live events carrying a private property, whatever week they fall in."""

        def page(page_token: str | None) -> Any:
            return self._events.list(
                calendarId=CALENDAR_ID,
                privateExtendedProperty=f"{name}={value}",
                showDeleted=False,
                singleEvents=False,
                maxResults=EVENT_PAGE_SIZE,
                **_page(page_token),
            )

        return [_entry_of(item) for item in _every_item(page, "items")]

    def event(self, event_id: str) -> CalendarEntry | None:
        item = execute_unless_missing(
            self._events.get(calendarId=CALENDAR_ID, eventId=event_id),
            missing_statuses=MISSING_EVENT_STATUSES,
        )
        return None if item is None else _entry_of(item)

    def cancel(self, event_id: str) -> None:
        execute(self._events.delete(calendarId=CALENDAR_ID, eventId=event_id, sendUpdates="none"))


def _every_item(page_of: Callable[[str | None], Any], key: str) -> Iterator[dict[str, Any]]:
    page_token: str | None = None
    while True:
        reply = execute(page_of(page_token))
        yield from reply.get(key, [])
        page_token = reply.get("nextPageToken")
        if not page_token:
            return


def _page(page_token: str | None) -> dict[str, str]:
    return {"pageToken": page_token} if page_token else {}


# A draft whose raw can't be decoded or parsed might be the lock, and a lock that can't be read
# refuses the reset. A draft that parses but carries neither the lock's subject nor its kind is not
# the lock: AECA writes both on every lock draft.
def _may_be_a_lock(reply: dict[str, Any]) -> bool:
    try:
        raw = base64.urlsafe_b64decode(reply["message"]["raw"] + "==")
        mime = message_from_bytes(raw, policy=policy.default)
        return bool(mime["Subject"] == LOCK_SUBJECT or mime["X-AECA-Kind"] == LOCK_KIND)
    except Exception:
        return True


def _heartbeat_in(reply: dict[str, Any]) -> datetime | None:
    try:
        holding = lock_holding_of(reply)
    except Exception:
        return None
    return None if holding is None else holding.heartbeat


def _entry_of(item: dict[str, Any]) -> CalendarEntry:
    start = item.get("start", {})
    return CalendarEntry(
        id=str(item.get("recurringEventId") or item["id"]),
        summary=str(item.get("summary", "")),
        starts=str(start.get("dateTime") or start.get("date") or ""),
        cancelled=item.get("status") == CANCELLED,
        private_properties=dict(item.get("extendedProperties", {}).get("private", {})),
    )
