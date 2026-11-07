"""The planner decides what the calendar offers, so its edges (buffers, zones, DST) are pinned."""

from datetime import UTC, date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from aeca.domain.approval import Hold
from aeca.domain.calendar_event import CalendarEvent
from aeca.domain.decision import Meeting, Window
from aeca.domain.meeting_outcome import SlotFree
from aeca.domain.planner import (
    AlternativesRequest,
    PlanSettings,
    SlotRequest,
    WorkHours,
    busy_intervals,
    choose_slot,
    find_alternatives,
    meeting_duration,
    parse_work_hours,
)
from aeca.domain.time_range import Interval

BUCHAREST = ZoneInfo("Europe/Bucharest")
PARIS = timezone(timedelta(hours=1))
EET = timezone(timedelta(hours=2))
MONDAY = date(2026, 11, 9)
OFFICE_HOURS = WorkHours(days=frozenset(range(5)), start=time(9, 0), end=time(17, 0))
HALF_AN_HOUR = timedelta(minutes=30)


def make_settings(
    work_hours: WorkHours = OFFICE_HOURS,
    buffer_minutes: int = 10,
    alternative_days: int = 5,
    zone: ZoneInfo = BUCHAREST,
) -> PlanSettings:
    return PlanSettings(
        zone=zone,
        work_hours=work_hours,
        meeting_minutes=30,
        buffer_minutes=buffer_minutes,
        alternative_days=alternative_days,
    )


def local(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=BUCHAREST)


def on_monday(hour: int, minute: int = 0) -> datetime:
    return local(MONDAY, hour, minute)


def at_day(offset: int, hour: int, minute: int = 0) -> datetime:
    return local(MONDAY + timedelta(days=offset), hour, minute)


def interval(start: datetime, end: datetime) -> Interval:
    return Interval(start=start, end=end)


def make_event(
    start: datetime,
    end: datetime,
    *,
    all_day: bool = False,
    cancelled: bool = False,
    declined_by_me: bool = False,
) -> CalendarEvent:
    return CalendarEvent(
        id="event-1",
        interval=interval(start, end),
        all_day=all_day,
        cancelled=cancelled,
        declined_by_me=declined_by_me,
        ical_uid=None,
    )


def window(start: datetime, end: datetime | None = None) -> Window:
    return Window(start=start, end=end, quote="proposed time")


def slot_request(
    windows: tuple[Window, ...],
    busy: tuple[Interval, ...] = (),
    settings: PlanSettings | None = None,
) -> SlotRequest:
    return SlotRequest(
        windows=windows,
        busy=busy,
        duration=HALF_AN_HOUR,
        settings=settings or make_settings(),
    )


def chosen_slot(request: SlotRequest) -> Interval:
    outcome = choose_slot(request)
    assert isinstance(outcome, SlotFree)
    return outcome.slot


def alternatives_request(
    now: datetime,
    busy: tuple[Interval, ...] = (),
    duration: timedelta = HALF_AN_HOUR,
    settings: PlanSettings | None = None,
) -> AlternativesRequest:
    return AlternativesRequest(
        now=now, busy=busy, duration=duration, settings=settings or make_settings()
    )


def whole_working_day(day_offset: int) -> Interval:
    return interval(at_day(day_offset, 9), at_day(day_offset, 17))


# --- choosing a slot ---


def test_the_earliest_free_slot_of_the_first_window_that_has_one() -> None:
    busy = (interval(on_monday(10), on_monday(11)),)
    windows = (
        window(on_monday(10), on_monday(10, 30)),
        window(on_monday(14), on_monday(16)),
        window(on_monday(10), on_monday(12)),
    )

    slot = chosen_slot(slot_request(windows, busy))

    assert slot.start == on_monday(14)
    assert slot.end == on_monday(14, 30)


def test_a_free_window_gives_its_own_start() -> None:
    slot = chosen_slot(slot_request((window(on_monday(10), on_monday(12)),)))

    assert slot == interval(on_monday(10), on_monday(10, 30))


def test_the_buffer_is_kept_around_events() -> None:
    event = make_event(on_monday(14), on_monday(15))
    request = slot_request(
        (window(on_monday(14), on_monday(17)),), busy_intervals([event], [], "t")
    )

    assert chosen_slot(request).start == on_monday(15, 10)


def test_the_buffer_is_kept_before_events_too() -> None:
    busy = (interval(on_monday(14), on_monday(15)),)
    request = slot_request((window(on_monday(13, 25), on_monday(13, 55)),), busy)

    assert choose_slot(request) is None


def test_a_slot_may_touch_the_edge_of_the_buffer() -> None:
    busy = (interval(on_monday(14), on_monday(15)),)
    request = slot_request((window(on_monday(13, 20), on_monday(13, 50)),), busy)

    assert chosen_slot(request) == interval(on_monday(13, 20), on_monday(13, 50))


def test_a_zero_buffer_lets_meetings_touch() -> None:
    busy = (interval(on_monday(14), on_monday(15)),)
    settings = make_settings(buffer_minutes=0)

    slot = chosen_slot(slot_request((window(on_monday(14), on_monday(16)),), busy, settings))

    assert slot.start == on_monday(15)


def test_an_all_day_event_blocks_the_day() -> None:
    all_day = make_event(on_monday(0), at_day(1, 0), all_day=True)
    busy = busy_intervals([all_day], [], "t")

    blocked_day = slot_request((window(on_monday(10), on_monday(12)),), busy)
    next_day = slot_request((window(at_day(1, 10), at_day(1, 12)),), busy)

    assert choose_slot(blocked_day) is None
    assert chosen_slot(next_day).start == at_day(1, 10)


def test_declined_and_cancelled_events_are_free() -> None:
    declined = make_event(on_monday(10), on_monday(11), declined_by_me=True)
    cancelled = make_event(on_monday(10), on_monday(11), cancelled=True)

    assert busy_intervals([declined, cancelled], [], "t") == ()


def test_tentative_and_unanswered_events_are_busy() -> None:
    event = make_event(on_monday(10), on_monday(11))

    assert busy_intervals([event], [], "t") == (event.interval,)


def test_holds_of_other_threads_are_busy_and_the_threads_own_are_free() -> None:
    own = Hold(thread_id="mine", approval_id="a1", interval=interval(on_monday(10), on_monday(11)))
    other = Hold(
        thread_id="theirs", approval_id="a2", interval=interval(on_monday(14), on_monday(15))
    )

    assert busy_intervals([], [own, other], "mine") == (other.interval,)


def test_a_slot_held_for_another_thread_is_not_offered() -> None:
    other = Hold(
        thread_id="theirs", approval_id="a2", interval=interval(on_monday(14), on_monday(15))
    )
    busy = busy_intervals([], [other], "mine")

    slot = chosen_slot(slot_request((window(on_monday(14), on_monday(17)),), busy))

    assert slot.start == on_monday(15, 10)


def test_no_windows_means_no_slot() -> None:
    assert choose_slot(slot_request(())) is None


def test_a_window_too_short_for_the_meeting_has_no_slot() -> None:
    assert choose_slot(slot_request((window(on_monday(10), on_monday(10, 20)),))) is None


# --- windows without an end ---


def test_a_window_without_an_end_uses_the_duration() -> None:
    slot = chosen_slot(slot_request((window(on_monday(10)),)))

    assert slot == interval(on_monday(10), on_monday(10, 30))


def test_a_window_without_an_end_offers_exactly_one_slot() -> None:
    busy = (interval(on_monday(10), on_monday(10, 15)),)

    assert choose_slot(slot_request((window(on_monday(10)),), busy)) is None


def test_a_busy_window_without_an_end_falls_through_to_the_next_window() -> None:
    busy = (interval(on_monday(10), on_monday(10, 15)),)
    windows = (window(on_monday(10)), window(on_monday(13)))

    assert chosen_slot(slot_request(windows, busy)).start == on_monday(13)


# --- work hours ---


def test_a_requested_slot_outside_work_hours_is_accepted_and_flagged() -> None:
    evening = choose_slot(slot_request((window(on_monday(20)),)))
    saturday = choose_slot(slot_request((window(at_day(5, 10)),)))
    working = choose_slot(slot_request((window(on_monday(10)),)))

    assert evening == SlotFree(
        slot=interval(on_monday(20), on_monday(20, 30)), outside_work_hours=True
    )
    assert isinstance(saturday, SlotFree)
    assert saturday.outside_work_hours is True
    assert isinstance(working, SlotFree)
    assert working.outside_work_hours is False


def test_a_slot_that_runs_past_closing_time_is_flagged() -> None:
    outcome = choose_slot(slot_request((window(on_monday(16, 45)),)))

    assert isinstance(outcome, SlotFree)
    assert outcome.outside_work_hours is True


def test_a_slot_that_ends_exactly_at_closing_time_is_inside() -> None:
    outcome = choose_slot(slot_request((window(on_monday(16, 30)),)))

    assert isinstance(outcome, SlotFree)
    assert outcome.outside_work_hours is False


def test_work_hours_are_read_in_the_calendars_zone_across_dst() -> None:
    before_dst_ends = datetime(2026, 10, 24, 6, 30, tzinfo=UTC)
    after_dst_ends = datetime(2026, 10, 25, 6, 30, tzinfo=UTC)
    every_day = WorkHours(days=frozenset(range(7)), start=time(9, 0), end=time(17, 0))
    settings = make_settings(work_hours=every_day)

    before = choose_slot(slot_request((window(before_dst_ends),), settings=settings))
    after = choose_slot(slot_request((window(after_dst_ends),), settings=settings))

    assert isinstance(before, SlotFree)
    assert before.outside_work_hours is False
    assert isinstance(after, SlotFree)
    assert after.outside_work_hours is True


def test_a_chosen_slot_is_expressed_in_the_calendars_zone() -> None:
    proposed_in_utc = datetime(2026, 11, 9, 8, 0, tzinfo=UTC)

    slot = chosen_slot(slot_request((window(proposed_in_utc),)))

    assert slot.start.utcoffset() == timedelta(hours=2)
    assert slot.end.utcoffset() == timedelta(hours=2)


# --- time zones ---


def test_a_window_in_another_zone_is_compared_as_an_instant() -> None:
    event = make_event(
        datetime(2026, 11, 9, 16, 0, tzinfo=EET), datetime(2026, 11, 9, 16, 30, tzinfo=EET)
    )
    busy = busy_intervals([event], [], "t")
    same_instant = datetime(2026, 11, 9, 15, 0, tzinfo=PARIS)
    request = slot_request((window(same_instant, same_instant + HALF_AN_HOUR),), busy)

    assert choose_slot(request) is None


def test_a_window_in_another_zone_that_is_free_as_an_instant_is_chosen() -> None:
    event = make_event(
        datetime(2026, 11, 9, 15, 0, tzinfo=EET), datetime(2026, 11, 9, 15, 30, tzinfo=EET)
    )
    busy = busy_intervals([event], [], "t")
    request = slot_request((window(datetime(2026, 11, 9, 15, 0, tzinfo=PARIS)),), busy)

    assert chosen_slot(request).start == datetime(2026, 11, 9, 16, 0, tzinfo=EET)


# --- duration ---


def test_duration_comes_from_the_email_else_meeting_minutes() -> None:
    settings = make_settings()
    stated = Meeting(title="Sync", duration_minutes=45, windows=())
    unstated = Meeting(title="Sync", duration_minutes=None, windows=())

    assert meeting_duration(stated, settings) == timedelta(minutes=45)
    assert meeting_duration(unstated, settings) == timedelta(minutes=30)


# --- alternatives ---


def test_alternatives_are_working_days_in_hours_within_five_days_and_at_most_three() -> None:
    found = find_alternatives(alternatives_request(on_monday(8)))

    assert found == (
        interval(at_day(0, 9), at_day(0, 9, 30)),
        interval(at_day(1, 9), at_day(1, 9, 30)),
        interval(at_day(2, 9), at_day(2, 9, 30)),
    )


def test_there_is_at_most_one_alternative_per_day() -> None:
    found = find_alternatives(alternatives_request(on_monday(8)))

    assert len({slot.start.date() for slot in found}) == len(found) == 3


def test_a_busy_day_is_skipped_and_the_search_moves_on() -> None:
    busy = (whole_working_day(0), whole_working_day(1))

    found = find_alternatives(alternatives_request(on_monday(8), busy))

    assert [slot.start for slot in found] == [at_day(2, 9), at_day(3, 9), at_day(4, 9)]


def test_only_the_next_alternative_days_working_days_are_searched() -> None:
    busy = tuple(whole_working_day(offset) for offset in range(4))

    found = find_alternatives(alternatives_request(on_monday(8), busy))

    assert [slot.start for slot in found] == [at_day(4, 9)]


def test_nothing_is_found_when_every_searched_day_is_busy() -> None:
    busy = tuple(whole_working_day(offset) for offset in range(5))

    assert find_alternatives(alternatives_request(on_monday(8), busy)) == ()


def test_weekends_are_skipped() -> None:
    saturday_morning = at_day(5, 10)

    found = find_alternatives(alternatives_request(saturday_morning))

    assert [slot.start for slot in found] == [at_day(7, 9), at_day(8, 9), at_day(9, 9)]


def test_today_counts_only_while_working_time_is_left() -> None:
    friday_evening = at_day(4, 18)
    next_week_busy = tuple(whole_working_day(offset) for offset in (7, 8, 9, 10))

    found = find_alternatives(alternatives_request(friday_evening, next_week_busy))

    assert [slot.start for slot in found] == [at_day(11, 9)]


def test_today_is_offered_while_working_time_is_left() -> None:
    found = find_alternatives(alternatives_request(on_monday(12, 10)))

    assert found[0] == interval(on_monday(12, 30), on_monday(13))


def test_an_alternative_starts_strictly_after_now() -> None:
    found = find_alternatives(alternatives_request(on_monday(12, 30)))

    assert found[0].start == on_monday(13)


def test_an_alternative_starts_on_the_hour_or_the_half_hour() -> None:
    busy = (interval(on_monday(9), on_monday(9, 25)),)

    found = find_alternatives(alternatives_request(on_monday(8), busy))

    assert found[0].start == on_monday(10)


def test_an_alternative_may_start_right_after_the_buffer() -> None:
    busy = (interval(on_monday(9), on_monday(9, 20)),)

    found = find_alternatives(alternatives_request(on_monday(8), busy))

    assert found[0].start == on_monday(9, 30)


def test_an_alternative_must_end_inside_work_hours() -> None:
    busy = (interval(on_monday(9), on_monday(16, 20)),)
    one_hour = timedelta(hours=1)

    found = find_alternatives(alternatives_request(on_monday(8), busy, duration=one_hour))

    assert found[0].start == at_day(1, 9)


def test_an_alternative_may_end_exactly_at_closing_time() -> None:
    busy = (interval(on_monday(9), on_monday(16, 20)),)

    found = find_alternatives(alternatives_request(on_monday(8), busy))

    assert found[0] == interval(on_monday(16, 30), on_monday(17))


def test_alternatives_respect_the_alternative_days_setting() -> None:
    settings = make_settings(alternative_days=2)

    found = find_alternatives(alternatives_request(on_monday(8), settings=settings))

    assert [slot.start for slot in found] == [at_day(0, 9), at_day(1, 9)]


def test_alternatives_use_the_calendars_work_hours_across_a_dst_change() -> None:
    every_day = WorkHours(days=frozenset(range(7)), start=time(9, 0), end=time(17, 0))
    settings = make_settings(work_hours=every_day)
    saturday_evening = datetime(2026, 10, 24, 20, 0, tzinfo=BUCHAREST)

    found = find_alternatives(alternatives_request(saturday_evening, settings=settings))

    first_day_after_the_change = datetime(2026, 10, 25, 9, 0, tzinfo=BUCHAREST)
    assert found[0].start == first_day_after_the_change
    assert found[0].start.utcoffset() == timedelta(hours=2)
    assert found[1].start == datetime(2026, 10, 26, 9, 0, tzinfo=BUCHAREST)
    assert found[0].start.astimezone(UTC) == datetime(2026, 10, 25, 7, 0, tzinfo=UTC)


def test_busy_time_in_another_zone_is_compared_as_an_instant_for_alternatives() -> None:
    nine_to_ten_local = interval(
        datetime(2026, 11, 9, 8, 0, tzinfo=PARIS), datetime(2026, 11, 9, 9, 0, tzinfo=PARIS)
    )

    found = find_alternatives(alternatives_request(on_monday(8), (nine_to_ten_local,)))

    assert found[0].start == on_monday(10, 30)


def test_a_slot_of_forty_five_minutes_is_found_on_the_half_hour() -> None:
    forty_five = timedelta(minutes=45)

    found = find_alternatives(alternatives_request(on_monday(8), duration=forty_five))

    assert found[0] == interval(on_monday(9), on_monday(9, 45))


# --- parsing work hours ---


@pytest.mark.parametrize(
    ("text", "days", "start", "end"),
    [
        ("Mon-Fri 09:00-17:00", {0, 1, 2, 3, 4}, time(9, 0), time(17, 0)),
        ("Mon 09:00-12:00", {0}, time(9, 0), time(12, 0)),
        ("Mon,Wed,Fri 09:00-17:00", {0, 2, 4}, time(9, 0), time(17, 0)),
        ("mon-fri 08:30-16:45", {0, 1, 2, 3, 4}, time(8, 30), time(16, 45)),
        ("SAT-SUN 10:00-14:00", {5, 6}, time(10, 0), time(14, 0)),
        ("Mon-Wed,Fri 09:00-17:00", {0, 1, 2, 4}, time(9, 0), time(17, 0)),
        ("  Tue,Thu   09:00-17:00 ", {1, 3}, time(9, 0), time(17, 0)),
    ],
)
def test_parse_work_hours(text: str, days: set[int], start: time, end: time) -> None:
    assert parse_work_hours(text) == WorkHours(days=frozenset(days), start=start, end=end)


@pytest.mark.parametrize(
    "text",
    [
        "",
        "Mon-Fri",
        "09:00-17:00",
        "Funday 09:00-17:00",
        "Mon-Fri 17:00-09:00",
        "Mon-Fri 09:00-09:00",
        "Mon-Fri 9:00-17:00",
        "Mon-Fri 09:00-25:00",
        "Mon-Fri 09:00-17:60",
        "Fri-Mon 09:00-17:00",
        "Mon-Fri 09:00-17:00 extra",
        "Mon,,Fri 09:00-17:00",
        "Mon- 09:00-17:00",
        "Mon-Fri 09:00",
    ],
)
def test_malformed_work_hours_are_refused_with_the_text_quoted(text: str) -> None:
    with pytest.raises(ValueError, match="work hours") as refusal:
        parse_work_hours(text)

    assert repr(text) in str(refusal.value)


def test_work_hours_without_days_are_refused() -> None:
    with pytest.raises(ValueError, match="day"):
        WorkHours(days=frozenset(), start=time(9, 0), end=time(17, 0))


def test_work_hours_that_end_before_they_start_are_refused() -> None:
    with pytest.raises(ValueError, match="end"):
        WorkHours(days=frozenset({0}), start=time(17, 0), end=time(9, 0))


# --- the autumn clock change, when one wall-clock hour happens twice ---

CLOCK_CHANGE_DAY = datetime(2026, 10, 25, tzinfo=UTC)


def utc_on_clock_change_day(hour: int, minute: int = 0) -> datetime:
    return CLOCK_CHANGE_DAY.replace(hour=hour, minute=minute)


def every_day_settings() -> PlanSettings:
    every_day = WorkHours(days=frozenset(range(7)), start=time(9, 0), end=time(17, 0))
    return make_settings(work_hours=every_day, buffer_minutes=0)


@pytest.mark.parametrize(("start_minute_utc", "minutes"), [(30, 60), (45, 30), (0, 120), (50, 20)])
def test_a_slot_that_crosses_the_clock_change_has_its_real_length(
    start_minute_utc: int, minutes: int
) -> None:
    start = utc_on_clock_change_day(0, start_minute_utc)
    request = SlotRequest(
        windows=(window(start),),
        busy=(),
        duration=timedelta(minutes=minutes),
        settings=every_day_settings(),
    )

    slot = chosen_slot(request)

    assert slot.end.astimezone(UTC) - slot.start.astimezone(UTC) == timedelta(minutes=minutes)


def test_a_window_across_the_clock_change_holds_a_one_hour_meeting() -> None:
    request = SlotRequest(
        windows=(window(utc_on_clock_change_day(0, 30), utc_on_clock_change_day(3, 30)),),
        busy=(),
        duration=timedelta(hours=1),
        settings=every_day_settings(),
    )

    slot = chosen_slot(request)

    assert slot.start.astimezone(UTC) == utc_on_clock_change_day(0, 30)
    assert slot.end.astimezone(UTC) == utc_on_clock_change_day(1, 30)


def test_an_event_in_the_repeated_hour_blocks_only_its_own_instants() -> None:
    zone = ZoneInfo("Europe/Bucharest")
    event_span = Interval(
        start=utc_on_clock_change_day(0, 30).astimezone(zone),
        end=utc_on_clock_change_day(1, 0).astimezone(zone),
    )
    request = SlotRequest(
        windows=(window(utc_on_clock_change_day(0, 0), utc_on_clock_change_day(3, 0)),),
        busy=(event_span,),
        duration=HALF_AN_HOUR,
        settings=every_day_settings(),
    )

    # Python calls an ambiguous local time unequal to its UTC twin, so compare in UTC.
    assert chosen_slot(request).start.astimezone(UTC) == utc_on_clock_change_day(0, 0)


def test_the_window_end_is_compared_as_an_instant_across_the_clock_change() -> None:
    zone = ZoneInfo("Europe/Bucharest")
    request = SlotRequest(
        windows=(
            window(
                utc_on_clock_change_day(0, 30).astimezone(zone),
                utc_on_clock_change_day(1, 0).astimezone(zone),
            ),
        ),
        busy=(),
        duration=timedelta(hours=1),
        settings=every_day_settings(),
    )

    assert choose_slot(request) is None


# --- settings are validated ---


@pytest.mark.parametrize(
    "overrides",
    [{"alternative_days": 0}, {"meeting_minutes": 0}, {"buffer_minutes": -1}],
)
def test_nonsense_plan_settings_are_refused(overrides: dict[str, int]) -> None:
    values = {"meeting_minutes": 30, "buffer_minutes": 10, "alternative_days": 5} | overrides

    with pytest.raises(ValueError, match="must be"):
        PlanSettings(zone=BUCHAREST, work_hours=OFFICE_HOURS, **values)


def test_a_zero_buffer_is_a_valid_setting() -> None:
    PlanSettings(
        zone=BUCHAREST,
        work_hours=OFFICE_HOURS,
        meeting_minutes=30,
        buffer_minutes=0,
        alternative_days=1,
    )


# --- alternatives when now arrives in UTC ---


def test_alternatives_work_when_now_is_given_in_utc() -> None:
    nine_fifteen_local_as_utc = datetime(2026, 11, 9, 7, 15, tzinfo=UTC)

    found = find_alternatives(alternatives_request(nine_fifteen_local_as_utc))

    assert found[0] == interval(on_monday(9, 30), on_monday(10))


def test_a_utc_now_just_before_local_midnight_still_means_the_local_day() -> None:
    sunday_23_30_utc_is_monday_01_30_local = datetime(2026, 11, 8, 23, 30, tzinfo=UTC)

    found = find_alternatives(alternatives_request(sunday_23_30_utc_is_monday_01_30_local))

    assert found[0].start == on_monday(9)
