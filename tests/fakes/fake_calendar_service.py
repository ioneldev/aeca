"""The primary calendar behind the googleapiclient shape, answering as the controller measured
Google does (2026-11-07):

- events.delete leaves the event readable by id with status cancelled;
- inserting an id that exists, live or cancelled, answers 409;
- events.update with a live status brings a cancelled event back.

events.list hides cancelled events unless showDeleted, filters on privateExtendedProperty, and
with singleEvents expands a weekly rule into instances `<master>_<yyyymmddThhmmssZ>` that carry
recurringEventId. It pages a few results at a time. Every write lands in the shared WriteJournal.
Each event carries a test-only made_by_rehearsal flag, kept beside it, as the oracle.
"""

from __future__ import annotations

import copy
import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any

from fakes.google_stub import http_error
from fakes.write_journal import CallSite, LazyRequest, ScriptedFailures, WriteJournal, WritesOnly

CALENDAR = "calendar"
CANCELLED = "cancelled"
NOT_FOUND = 404
GONE = 410
ALREADY_EXISTS = 409
WEEKLY_RULE = re.compile(r"RRULE:FREQ=WEEKLY;COUNT=(\d+)")


@dataclass
class FakeCalendarService:
    journal: WriteJournal
    page_size: int = 2
    failures: ScriptedFailures = field(default_factory=ScriptedFailures)
    stored: dict[str, dict[str, Any]] = field(default_factory=dict)
    made_by_rehearsal: dict[str, bool] = field(default_factory=dict)

    def add(self, body: dict[str, Any], *, made_by_rehearsal: bool) -> None:
        self.stored[body["id"]] = {"status": "confirmed", **copy.deepcopy(body)}
        self.made_by_rehearsal[body["id"]] = made_by_rehearsal

    def status_of(self, event_id: str) -> str:
        return str(self.stored[event_id]["status"])

    def events(self) -> _Events:
        return _Events(self)

    def calendarList(self) -> WritesOnly:
        return WritesOnly(self.journal, CallSite(CALENDAR, "calendarList", "calendarId"))

    def settings(self) -> WritesOnly:
        return WritesOnly(self.journal, CallSite(CALENDAR, "settings", "setting"))

    def acl(self) -> WritesOnly:
        return WritesOnly(self.journal, CallSite(CALENDAR, "acl", "ruleId"))


class _Events:
    def __init__(self, calendar: FakeCalendarService) -> None:
        self._calendar = calendar

    def list(self, **arguments: Any) -> LazyRequest:
        return LazyRequest(lambda: self._list(arguments))

    def get(self, *, calendarId: str, eventId: str) -> LazyRequest:
        return LazyRequest(lambda: self._get(eventId))

    def delete(
        self, *, calendarId: str, eventId: str, sendUpdates: str | None = None
    ) -> LazyRequest:
        return LazyRequest(lambda: self._delete(eventId))

    def insert(
        self, *, calendarId: str, body: dict[str, Any], sendUpdates: str | None = None
    ) -> LazyRequest:
        return LazyRequest(lambda: self._insert(body))

    def update(
        self,
        *,
        calendarId: str,
        eventId: str,
        body: dict[str, Any],
        sendUpdates: str | None = None,
    ) -> LazyRequest:
        return LazyRequest(lambda: self._update(eventId, body))

    def __getattr__(self, name: str) -> Callable[..., LazyRequest]:
        site = CallSite(CALENDAR, "events", "eventId")
        return getattr(WritesOnly(self._calendar.journal, site), name)

    def _list(self, arguments: dict[str, Any]) -> dict[str, Any]:
        shown = [
            item
            for item in self._listed(arguments.get("singleEvents", False))
            if (arguments.get("showDeleted", False) or item["status"] != CANCELLED)
            and _has_property(item, arguments)
            and _within(item, arguments)
        ]
        start = int(arguments.get("pageToken") or 0)
        page_size = self._calendar.page_size
        reply: dict[str, Any] = {"items": copy.deepcopy(shown[start : start + page_size])}
        if start + page_size < len(shown):
            reply["nextPageToken"] = str(start + page_size)
        return reply

    def _listed(self, single_events: bool) -> list[dict[str, Any]]:
        if not single_events:
            return list(self._calendar.stored.values())
        return [
            instance for item in self._calendar.stored.values() for instance in _instances(item)
        ]

    def _get(self, event_id: str) -> dict[str, Any]:
        item = self._calendar.stored.get(event_id)
        if item is None:
            raise http_error(NOT_FOUND)
        return copy.deepcopy(item)

    def _delete(self, event_id: str) -> dict[str, Any]:
        self._calendar.journal.write(CALENDAR, "events.delete", event_id)
        self._calendar.failures.check("events.delete", event_id)
        item = self._calendar.stored.get(event_id)
        if item is None:
            raise http_error(NOT_FOUND)
        if item["status"] == CANCELLED:
            raise http_error(GONE)
        item["status"] = CANCELLED
        return {}

    def _insert(self, body: dict[str, Any]) -> dict[str, Any]:
        self._calendar.journal.write(CALENDAR, "events.insert", body["id"])
        if body["id"] in self._calendar.stored:
            raise http_error(ALREADY_EXISTS)
        self._calendar.stored[body["id"]] = {"status": "confirmed", **copy.deepcopy(body)}
        return copy.deepcopy(self._calendar.stored[body["id"]])

    def _update(self, event_id: str, body: dict[str, Any]) -> dict[str, Any]:
        self._calendar.journal.write(CALENDAR, "events.update", event_id)
        if event_id not in self._calendar.stored:
            raise http_error(NOT_FOUND)
        self._calendar.stored[event_id] = {"status": "confirmed", **copy.deepcopy(body)}
        self._calendar.stored[event_id]["id"] = event_id
        return copy.deepcopy(self._calendar.stored[event_id])


def _instances(item: dict[str, Any]) -> Iterator[dict[str, Any]]:
    rule = next((WEEKLY_RULE.fullmatch(r) for r in item.get("recurrence", [])), None)
    if rule is None:
        yield item
        return
    first = _start_of(item)
    for week in range(int(rule.group(1))):
        start = first + timedelta(weeks=week)
        yield {
            **copy.deepcopy(item),
            "id": f"{item['id']}_{start.astimezone(UTC):%Y%m%dT%H%M%SZ}",
            "recurringEventId": item["id"],
            "start": {"dateTime": start.isoformat()},
        }


def _has_property(item: dict[str, Any], arguments: dict[str, Any]) -> bool:
    wanted = arguments.get("privateExtendedProperty")
    if wanted is None:
        return True
    name, _, value = str(wanted).partition("=")
    return bool(item.get("extendedProperties", {}).get("private", {}).get(name) == value)


def _within(item: dict[str, Any], arguments: dict[str, Any]) -> bool:
    if "start" not in item:
        return True
    start = _start_of(item)
    time_min, time_max = arguments.get("timeMin"), arguments.get("timeMax")
    after_min = time_min is None or start >= datetime.fromisoformat(time_min)
    before_max = time_max is None or start < datetime.fromisoformat(time_max)
    return after_min and before_max


def _start_of(item: dict[str, Any]) -> datetime:
    start = item["start"]
    if "dateTime" in start:
        return datetime.fromisoformat(start["dateTime"])
    return datetime.combine(date.fromisoformat(start["date"]), datetime.min.time(), tzinfo=UTC)
