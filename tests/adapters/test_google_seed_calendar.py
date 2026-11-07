from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from fakes.google_stub import GoogleStub

from aeca.adapters.google_seed_calendar import GoogleSeedCalendar
from aeca.tools.seed_calendar import SeedEvent

ZONE = ZoneInfo("Europe/Bucharest")
ACCOUNT = "aeca@gmail.test"


def timed(**overrides: Any) -> SeedEvent:
    fields: dict[str, Any] = {
        "summary": "[AECA test] Focus block",
        "start": datetime(2026, 11, 9, 10, 0, tzinfo=ZONE),
        "end": datetime(2026, 11, 9, 11, 0, tzinfo=ZONE),
        "zone": "Europe/Bucharest",
        "private_properties": {"aeca_test": "true"},
    }
    return SeedEvent(**{**fields, **overrides})


def insert(event: SeedEvent) -> tuple[str, dict[str, Any]]:
    stub = GoogleStub()
    stub.reply("events.insert", {"id": "google-id-1"})
    created = GoogleSeedCalendar(stub, ACCOUNT).insert(event)
    return created, stub.calls_to("events.insert")[0]


def test_a_timed_event_goes_in_with_its_tag_and_no_invitations() -> None:
    created, call = insert(timed())

    assert created == "google-id-1"
    assert call["sendUpdates"] == "none"
    body = call["body"]
    assert body["summary"] == "[AECA test] Focus block"
    assert body["extendedProperties"] == {"private": {"aeca_test": "true"}}
    assert body["start"] == {
        "dateTime": "2026-11-09T10:00:00+02:00",
        "timeZone": "Europe/Bucharest",
    }
    assert body["end"]["dateTime"] == "2026-11-09T11:00:00+02:00"


def test_an_all_day_event_uses_dates() -> None:
    _, call = insert(timed(start=date(2026, 11, 11), end=date(2026, 11, 12)))

    assert call["body"]["start"] == {"date": "2026-11-11"}
    assert call["body"]["end"] == {"date": "2026-11-12"}


def test_a_recurring_event_carries_its_rule() -> None:
    _, call = insert(timed(recurrence=("RRULE:FREQ=WEEKLY;COUNT=4",)))

    assert call["body"]["recurrence"] == ["RRULE:FREQ=WEEKLY;COUNT=4"]


def test_a_tentative_event_says_so() -> None:
    _, call = insert(timed(status="tentative"))

    assert call["body"]["status"] == "tentative"


def test_a_declined_event_lists_the_account_as_a_declined_attendee() -> None:
    _, call = insert(timed(declined_by_me=True))

    assert call["body"]["attendees"] == [
        {"email": ACCOUNT, "self": True, "responseStatus": "declined"}
    ]


def test_an_ordinary_event_has_no_attendees_or_recurrence() -> None:
    _, call = insert(timed())

    assert "attendees" not in call["body"] and "recurrence" not in call["body"]
