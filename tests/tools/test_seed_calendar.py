import re
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from fakes.fake_calendar_service import FakeCalendarService
from fakes.memory_seed_calendar import MemorySeedCalendar
from fakes.write_journal import WriteJournal

from aeca.adapters.google_seed_calendar import GoogleSeedCalendar
from aeca.tools.seed_calendar import SeedWeek, next_monday, seed_calendar

BUCHAREST = ZoneInfo("Europe/Bucharest")
MONDAY = date(2026, 11, 9)
ACCOUNT = "aeca@gmail.test"


def seed() -> MemorySeedCalendar:
    calendar = MemorySeedCalendar()
    seed_calendar(calendar, SeedWeek(week_start=MONDAY, zone=BUCHAREST))
    return calendar


def on_day(calendar: MemorySeedCalendar, offset: int) -> list:  # type: ignore[type-arg]
    day = MONDAY + timedelta(days=offset)
    return [event for event in calendar.events if _date_of(event.start) == day]


def _date_of(moment: date) -> date:
    return moment.date() if isinstance(moment, datetime) else moment


def test_seeded_events_are_tagged() -> None:
    calendar = seed()

    assert calendar.events
    for event in calendar.events:
        assert event.private_properties["aeca_test"] == "true"
        assert event.summary.startswith("[AECA test]")


def test_seeding_reports_the_ids_it_created() -> None:
    calendar = MemorySeedCalendar()

    report = seed_calendar(calendar, SeedWeek(week_start=MONDAY, zone=BUCHAREST))

    assert report.created == [event.event_id for event in calendar.events]
    assert report.skipped == []


def test_seeding_twice_creates_nothing_the_second_time() -> None:
    calendar = MemorySeedCalendar()
    week = SeedWeek(week_start=MONDAY, zone=BUCHAREST)
    first = seed_calendar(calendar, week)

    second = seed_calendar(calendar, week)

    assert second.created == [] and second.skipped == first.created
    assert len(calendar.events) == len(first.created)


def test_a_different_week_gets_different_ids() -> None:
    calendar = MemorySeedCalendar()
    seed_calendar(calendar, SeedWeek(week_start=MONDAY, zone=BUCHAREST))

    later = seed_calendar(calendar, SeedWeek(week_start=date(2026, 11, 16), zone=BUCHAREST))

    assert later.skipped == []


def test_event_ids_are_valid_google_ids_and_unique() -> None:
    ids = [event.event_id for event in seed().events]

    assert len(set(ids)) == len(ids)
    for event_id in ids:
        assert re.fullmatch(r"[0-9a-v]{5,1024}", event_id)
        assert event_id.startswith("aecaseed")


def test_every_working_day_has_a_focus_block_from_ten_to_eleven() -> None:
    calendar = seed()

    for offset in range(5):
        focus = [e for e in on_day(calendar, offset) if "Focus block" in e.summary]
        assert len(focus) == 1
        assert (focus[0].start.hour, focus[0].end.hour) == (10, 11)  # type: ignore[union-attr]


def test_nothing_is_seeded_outside_monday_to_friday() -> None:
    calendar = seed()

    assert on_day(calendar, 5) == [] and on_day(calendar, 6) == []
    assert all(_date_of(event.start) <= MONDAY + timedelta(days=4) for event in calendar.events)


def test_tuesday_has_a_weekly_sync_that_recurs_four_times() -> None:
    sync = [e for e in on_day(seed(), 1) if "Weekly sync" in e.summary]

    assert len(sync) == 1
    assert sync[0].recurrence == ("RRULE:FREQ=WEEKLY;COUNT=4",)
    assert (sync[0].start.hour, sync[0].end.hour) == (14, 15)  # type: ignore[union-attr]


def test_wednesday_has_an_all_day_offsite() -> None:
    offsite = [e for e in on_day(seed(), 2) if "Offsite" in e.summary]

    assert len(offsite) == 1
    assert offsite[0].start == date(2026, 11, 11)
    assert not isinstance(offsite[0].start, datetime)
    assert offsite[0].end == date(2026, 11, 12)


def test_thursday_is_booked_solid_from_nine_to_five() -> None:
    hourly = [e for e in on_day(seed(), 3) if "Focus block" not in e.summary]

    starts = sorted(e.start.hour for e in hourly)  # type: ignore[union-attr]
    assert starts == list(range(9, 17))
    assert all(e.end - e.start == timedelta(hours=1) for e in hourly)  # type: ignore[operator]


def test_friday_has_a_tentative_vendor_call_in_the_afternoon() -> None:
    vendor = [e for e in on_day(seed(), 4) if "Vendor call" in e.summary]

    assert len(vendor) == 1 and vendor[0].status == "tentative"
    assert (vendor[0].start.hour, vendor[0].end.hour) == (15, 16)  # type: ignore[union-attr]


def test_monday_has_a_declined_event_at_one_that_the_account_declined() -> None:
    declined = [e for e in on_day(seed(), 0) if e.declined_by_me]

    assert len(declined) == 1
    assert (declined[0].start.hour, declined[0].end.hour) == (13, 14)  # type: ignore[union-attr]


def test_timed_events_carry_the_calendar_zone() -> None:
    timed = [e for e in seed().events if isinstance(e.start, datetime)]

    assert all(e.zone == "Europe/Bucharest" for e in timed)
    assert all(e.start.utcoffset() == timedelta(hours=2) for e in timed)  # type: ignore[union-attr]


def test_next_monday_from_a_saturday_is_the_coming_monday() -> None:
    saturday = datetime(2026, 11, 7, 12, 0, tzinfo=ZoneInfo("UTC"))

    assert next_monday(saturday, BUCHAREST) == MONDAY


def test_next_monday_from_a_monday_is_a_week_later() -> None:
    monday = datetime(2026, 11, 9, 8, 0, tzinfo=ZoneInfo("UTC"))

    assert next_monday(monday, BUCHAREST) == date(2026, 11, 16)


def test_next_monday_reads_the_date_in_the_calendar_zone_not_utc() -> None:
    late_sunday_utc = datetime(2026, 11, 8, 23, 0, tzinfo=ZoneInfo("UTC"))  # Monday 01:00 local

    assert next_monday(late_sunday_utc, BUCHAREST) == date(2026, 11, 16)


# After make reset-test the seeded events are cancelled, and Google answers 409 to an insert under a
# cancelled id (measured 2026-11-07), so a second rehearsal's re-seed has to bring them back.


def seeded_in_google() -> FakeCalendarService:
    calendar = FakeCalendarService(journal=WriteJournal())
    reseed(calendar)
    calendar.journal.clear()
    return calendar


def cancel(calendar: FakeCalendarService, event_ids: list[str]) -> None:
    for event_id in event_ids:
        calendar.events().delete(calendarId="primary", eventId=event_id).execute()
    calendar.journal.clear()


def reseed(calendar: FakeCalendarService):  # type: ignore[no-untyped-def]
    return seed_calendar(
        GoogleSeedCalendar(calendar, ACCOUNT), SeedWeek(week_start=MONDAY, zone=BUCHAREST)
    )


def test_reseeding_a_week_a_reset_cancelled_brings_every_event_back_as_seeded() -> None:
    calendar = seeded_in_google()
    as_seeded = {event_id: dict(item) for event_id, item in calendar.stored.items()}
    cancel(calendar, list(as_seeded))

    report = reseed(calendar)

    assert calendar.stored == as_seeded
    assert sorted(report.created) == sorted(as_seeded) and report.skipped == []


def test_the_tentative_seed_event_comes_back_tentative() -> None:
    calendar = seeded_in_google()
    cancel(calendar, list(calendar.stored))

    reseed(calendar)

    statuses = {item["summary"]: item["status"] for item in calendar.stored.values()}
    assert statuses["[AECA test] Vendor call (tentative)"] == "tentative"
    assert statuses["[AECA test] Weekly sync"] == "confirmed"


def test_only_the_cancelled_events_are_rewritten() -> None:
    calendar = seeded_in_google()
    [first, *live] = list(calendar.stored)
    cancel(calendar, [first])

    report = reseed(calendar)

    writes = calendar.journal.mutations()
    assert [target for _, method, target in writes if method == "events.update"] == [first]
    assert report.created == [first] and sorted(report.skipped) == sorted(live)


def test_a_live_week_is_skipped_and_never_rewritten() -> None:
    calendar = seeded_in_google()

    report = reseed(calendar)

    methods = {method for _, method, _ in calendar.journal.mutations()}
    assert report.created == [] and len(report.skipped) == len(calendar.stored)
    assert methods == {"events.insert"}
