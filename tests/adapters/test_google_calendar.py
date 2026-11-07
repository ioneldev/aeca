from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from fakes.google_stub import GoogleStub, http_error

from aeca.adapters.google_calendar import GoogleCalendar
from aeca.domain.calendar_event import NewEvent
from aeca.domain.errors import EmailError, SystemWideError
from aeca.domain.time_range import Interval

BUCHAREST = ZoneInfo("Europe/Bucharest")
WINDOW = Interval(datetime(2026, 11, 9, 0, 0, tzinfo=UTC), datetime(2026, 11, 10, 0, 0, tzinfo=UTC))


def calendar_in(zone: str = "Europe/Bucharest") -> tuple[GoogleCalendar, GoogleStub]:
    stub = GoogleStub()
    stub.reply("settings.get", {"value": zone})
    return GoogleCalendar(stub), stub


def timed_event(**overrides: Any) -> dict[str, Any]:
    event = {
        "id": "e1",
        "iCalUID": "e1@google.com",
        "status": "confirmed",
        "start": {"dateTime": "2026-11-09T10:00:00+02:00"},
        "end": {"dateTime": "2026-11-09T11:00:00+02:00"},
    }
    return event | overrides


def test_time_zone_is_read_once_from_the_calendar_settings() -> None:
    calendar, stub = calendar_in("Europe/Berlin")

    assert calendar.time_zone() == "Europe/Berlin"
    assert calendar.time_zone() == "Europe/Berlin"
    assert stub.calls_to("settings.get") == [{"setting": "timezone"}]


def test_events_are_listed_as_single_events_in_the_window() -> None:
    calendar, stub = calendar_in()
    stub.reply("events.list", {"items": [timed_event()]})

    events = calendar.events(WINDOW)

    (asked,) = stub.calls_to("events.list")
    assert asked["calendarId"] == "primary"
    assert asked["singleEvents"] is True
    assert asked["showDeleted"] is False
    assert asked["timeMin"] == "2026-11-09T00:00:00+00:00"
    assert asked["timeMax"] == "2026-11-10T00:00:00+00:00"
    assert [event.id for event in events] == ["e1"]
    assert events[0].interval.start == datetime(2026, 11, 9, 8, 0, tzinfo=UTC)
    assert events[0].ical_uid == "e1@google.com"


def test_events_page_through_every_result() -> None:
    calendar, stub = calendar_in()
    stub.reply(
        "events.list",
        {"items": [timed_event(id="a")], "nextPageToken": "t2"},
        {"items": [timed_event(id="b")]},
    )

    events = calendar.events(WINDOW)

    assert [event.id for event in events] == ["a", "b"]
    assert [call.get("pageToken") for call in stub.calls_to("events.list")] == [None, "t2"]


def test_all_day_and_declined_events_convert() -> None:
    calendar, stub = calendar_in("Europe/Bucharest")
    all_day = timed_event(id="holiday", start={"date": "2026-11-09"}, end={"date": "2026-11-10"})
    declined = timed_event(
        id="declined",
        attendees=[
            {"email": "other@x.com", "responseStatus": "accepted"},
            {"email": "me@x.com", "self": True, "responseStatus": "declined"},
        ],
    )
    stub.reply("events.list", {"items": [all_day, declined]})

    holiday, declined_event = calendar.events(WINDOW)

    assert holiday.all_day is True
    assert holiday.interval == Interval(
        datetime(2026, 11, 9, tzinfo=BUCHAREST), datetime(2026, 11, 10, tzinfo=BUCHAREST)
    )
    assert holiday.interval.start == datetime(2026, 11, 8, 22, 0, tzinfo=UTC)
    assert holiday.declined_by_me is False
    assert declined_event.declined_by_me is True
    assert declined_event.all_day is False


def test_someone_elses_decline_is_not_mine() -> None:
    calendar, stub = calendar_in()
    other = timed_event(attendees=[{"email": "o@x.com", "responseStatus": "declined"}])
    stub.reply("events.list", {"items": [other]})

    assert calendar.events(WINDOW)[0].declined_by_me is False


def test_a_zero_length_event_still_converts() -> None:
    calendar, stub = calendar_in()
    instant = timed_event(end={"dateTime": "2026-11-09T10:00:00+02:00"})
    stub.reply("events.list", {"items": [instant]})

    (event,) = calendar.events(WINDOW)

    assert event.interval.end > event.interval.start


def test_get_event_returns_a_cancelled_event() -> None:
    calendar, stub = calendar_in()
    stub.reply("events.get", {"id": "gone", "status": "cancelled"})

    event = calendar.get_event("gone")

    assert event is not None
    assert event.cancelled is True
    assert stub.calls_to("events.get") == [{"calendarId": "primary", "eventId": "gone"}]


@pytest.mark.parametrize("status", [404, 410])
def test_get_event_is_none_when_the_event_does_not_exist(status: int) -> None:
    calendar, stub = calendar_in()
    stub.reply("events.get", http_error(status))

    assert calendar.get_event("nope") is None


def test_get_event_surfaces_a_system_wide_error() -> None:
    calendar, stub = calendar_in()
    stub.reply("events.get", http_error(503))

    with pytest.raises(SystemWideError):
        calendar.get_event("e1")


def test_event_by_ical_uid_returns_the_first_match_or_none() -> None:
    calendar, stub = calendar_in()
    stub.reply("events.list", {"items": [timed_event()]}, {"items": []})

    found = calendar.event_by_ical_uid("e1@google.com")
    missing = calendar.event_by_ical_uid("zzz")

    assert found is not None
    assert found.id == "e1"
    assert missing is None
    assert stub.calls_to("events.list")[0]["iCalUID"] == "e1@google.com"


def test_insert_event_has_no_attendees_and_sends_no_updates() -> None:
    calendar, stub = calendar_in("Europe/Bucharest")
    stub.reply("events.insert", {"id": "abc"})
    event = NewEvent(
        id="abc",
        title="Call",
        description="why",
        interval=Interval(
            datetime(2026, 11, 9, 8, 0, tzinfo=UTC), datetime(2026, 11, 9, 8, 30, tzinfo=UTC)
        ),
    )

    calendar.insert_event(event)

    (sent,) = stub.calls_to("events.insert")
    assert sent["calendarId"] == "primary"
    assert sent["sendUpdates"] == "none"
    assert sent["body"] == {
        "id": "abc",
        "summary": "Call",
        "description": "why",
        "start": {"dateTime": "2026-11-09T10:00:00+02:00", "timeZone": "Europe/Bucharest"},
        "end": {"dateTime": "2026-11-09T10:30:00+02:00", "timeZone": "Europe/Bucharest"},
    }
    assert "attendees" not in sent["body"]


def test_insert_failure_is_classified() -> None:
    calendar, stub = calendar_in()
    stub.reply("events.insert", http_error(400))
    event = NewEvent("abc", "t", "d", WINDOW)

    with pytest.raises(EmailError):
        calendar.insert_event(event)
