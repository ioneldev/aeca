from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

import httplib2
import pytest
from fakes.google_stub import GoogleStub
from googleapiclient.errors import HttpError

from aeca.adapters.google_seed_calendar import GoogleSeedCalendar, SeedInsert
from aeca.domain.errors import AecaError
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
        "event_id": "aecaseed0abc",
    }
    return SeedEvent(**{**fields, **overrides})


def insert(event: SeedEvent) -> tuple[SeedInsert, dict[str, Any]]:
    stub = GoogleStub()
    stub.reply("events.insert", {"id": event.event_id})
    inserted = GoogleSeedCalendar(stub, ACCOUNT).insert(event)
    return inserted, stub.calls_to("events.insert")[0]


def http_error(status: int) -> HttpError:
    return HttpError(httplib2.Response({"status": status}), b"{}")


def test_a_timed_event_goes_in_with_its_tag_and_no_invitations() -> None:
    created, call = insert(timed())

    assert created == SeedInsert(event_id="aecaseed0abc", created=True)
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


def test_the_event_goes_in_under_its_own_id() -> None:
    _, call = insert(timed())

    assert call["body"]["id"] == "aecaseed0abc"


def test_google_refusing_a_duplicate_id_of_a_live_event_means_it_is_already_seeded() -> None:
    stub = GoogleStub()
    stub.reply("events.insert", http_error(409))
    stub.reply("events.get", {"id": "aecaseed0abc", "status": "confirmed"})

    inserted = GoogleSeedCalendar(stub, ACCOUNT).insert(timed())

    assert inserted == SeedInsert(event_id="aecaseed0abc", created=False)
    assert "events.update" not in stub.paths_called()


def test_a_duplicate_id_of_a_cancelled_event_is_brought_back_with_the_seed_body() -> None:
    stub = GoogleStub()
    stub.reply("events.insert", http_error(409))
    stub.reply("events.get", {"id": "aecaseed0abc", "status": "cancelled"})
    stub.reply("events.update", {"id": "aecaseed0abc", "status": "tentative"})

    inserted = GoogleSeedCalendar(stub, ACCOUNT).insert(timed(status="tentative"))

    [update] = stub.calls_to("events.update")
    [insert] = stub.calls_to("events.insert")
    assert inserted == SeedInsert(event_id="aecaseed0abc", created=True)
    assert update["eventId"] == "aecaseed0abc" and update["sendUpdates"] == "none"
    assert update["body"] == insert["body"]
    assert update["body"]["status"] == "tentative"


def test_a_failure_to_bring_a_cancelled_event_back_raises() -> None:
    stub = GoogleStub()
    stub.reply("events.insert", http_error(409))
    stub.reply("events.get", {"id": "aecaseed0abc", "status": "cancelled"})
    stub.reply("events.update", http_error(500))

    with pytest.raises(AecaError):
        GoogleSeedCalendar(stub, ACCOUNT).insert(timed())


def test_any_other_google_failure_still_raises() -> None:
    stub = GoogleStub()
    stub.reply("events.insert", http_error(500))

    with pytest.raises(AecaError):
        GoogleSeedCalendar(stub, ACCOUNT).insert(timed())


def test_a_second_run_against_google_creates_nothing() -> None:
    stub = GoogleStub()
    stub.reply("events.insert", {"id": "aecaseed0abc"}, http_error(409))
    stub.reply("events.get", {"id": "aecaseed0abc", "status": "confirmed"})
    calendar = GoogleSeedCalendar(stub, ACCOUNT)

    first, second = calendar.insert(timed()), calendar.insert(timed())

    assert (first.created, second.created) == (True, False)
