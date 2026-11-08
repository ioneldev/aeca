"""plan_meeting asks the calendar in the spec's order: the invitation, then the proposed windows,
then the alternatives, and reads only the span each step needs."""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from fakes.fake_calendar import FakeCalendar
from support.decisions import decision_of
from support.facts import make_facts

from aeca.application.meetings import MeetingRequest, plan_meeting
from aeca.domain.approval import Hold
from aeca.domain.calendar_event import CalendarEvent
from aeca.domain.decision import Decision, Meeting, Window
from aeca.domain.labels import Label
from aeca.domain.meeting_outcome import AlreadyOnCalendar, NoSlotFree, SlotFree
from aeca.domain.planner import PlanSettings, parse_work_hours
from aeca.domain.time_range import Interval

BUCHAREST = ZoneInfo("Europe/Bucharest")
# Monday 9 November 2026, 08:00 in Bucharest.
NOW = datetime(2026, 11, 9, 6, 0, tzinfo=UTC)
SETTINGS = PlanSettings(
    zone=BUCHAREST,
    work_hours=parse_work_hours("Mon-Fri 09:00-17:00"),
    meeting_minutes=30,
    buffer_minutes=10,
    alternative_days=5,
)
TUESDAY_3PM = datetime(2026, 11, 10, 15, 0, tzinfo=BUCHAREST)
UID = "invite-1@example.com"
TEN_MINUTES = timedelta(minutes=10)


def at(day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 11, day, hour, minute, tzinfo=BUCHAREST)


def event(
    start: datetime, end: datetime, *, ical_uid: str | None = None, cancelled: bool = False
) -> CalendarEvent:
    return CalendarEvent(
        id=f"ev-{start.isoformat()}",
        interval=Interval(start, end),
        all_day=False,
        cancelled=cancelled,
        declined_by_me=False,
        ical_uid=ical_uid,
    )


def meeting_at(*starts: datetime) -> Decision:
    windows = tuple(Window(start, None, "quoted") for start in starts)
    return decision_of(
        Label.MEETING_REQUEST, meeting=Meeting(title="Sync", duration_minutes=None, windows=windows)
    )


def request(
    decision: Decision,
    calendar: FakeCalendar,
    *,
    holds: tuple[Hold, ...] = (),
    ical_uid: str | None = None,
) -> MeetingRequest:
    return MeetingRequest(
        facts=make_facts(ical_uid=ical_uid, thread_id="this-thread"),
        decision=decision,
        calendar=calendar,
        holds=holds,
        settings=SETTINGS,
        now=NOW,
    )


def test_a_decision_that_is_not_a_meeting_plans_nothing() -> None:
    calendar = FakeCalendar()

    assert plan_meeting(request(decision_of(Label.TASK), calendar)) is None
    assert plan_meeting(request(decision_of(Label.MEETING_REQUEST), calendar)) is None
    assert calendar.event_reads == [] and calendar.uid_reads == []


def test_an_invitation_on_the_calendar_comes_first_and_reads_nothing_else() -> None:
    calendar = FakeCalendar()
    calendar.add(event(at(10, 15), at(10, 16), ical_uid=UID))

    outcome = plan_meeting(request(meeting_at(TUESDAY_3PM), calendar, ical_uid=UID))

    assert outcome == AlreadyOnCalendar(event_id=f"ev-{at(10, 15).isoformat()}")
    assert calendar.event_reads == []


def test_a_cancelled_invitation_is_planned_like_any_request() -> None:
    calendar = FakeCalendar()
    calendar.add(event(at(10, 15), at(10, 16), ical_uid=UID, cancelled=True))

    outcome = plan_meeting(request(meeting_at(TUESDAY_3PM), calendar, ical_uid=UID))

    assert isinstance(outcome, SlotFree)


def test_a_free_proposed_slot_reads_only_the_windows_span_with_the_buffer() -> None:
    calendar = FakeCalendar()

    outcome = plan_meeting(request(meeting_at(TUESDAY_3PM, at(11, 10)), calendar))

    assert outcome == SlotFree(slot=Interval(at(10, 15), at(10, 15, 30)), outside_work_hours=False)
    padded_span = Interval(at(10, 15) - TEN_MINUTES, at(11, 10, 30) + TEN_MINUTES)
    assert calendar.event_reads == [padded_span]


def test_an_event_inside_the_buffer_makes_the_proposed_slot_busy() -> None:
    calendar = FakeCalendar()
    calendar.add(event(at(10, 14), at(10, 14, 55)))

    outcome = plan_meeting(request(meeting_at(TUESDAY_3PM), calendar))

    assert isinstance(outcome, NoSlotFree)


def test_busy_proposed_times_give_alternatives_from_the_next_working_days() -> None:
    calendar = FakeCalendar()
    calendar.add(event(at(10, 15), at(10, 16)))

    outcome = plan_meeting(request(meeting_at(TUESDAY_3PM), calendar))

    assert outcome == NoSlotFree(
        alternatives=(
            Interval(at(9, 9), at(9, 9, 30)),
            Interval(at(10, 9), at(10, 9, 30)),
            Interval(at(11, 9), at(11, 9, 30)),
        )
    )
    # Monday to Friday: the five working days from now, to Friday's closing time.
    assert calendar.event_reads[-1] == Interval(NOW - TEN_MINUTES, at(13, 17) + TEN_MINUTES)


def test_no_proposed_time_goes_straight_to_the_alternatives() -> None:
    calendar = FakeCalendar()

    outcome = plan_meeting(request(meeting_at(), calendar))

    assert isinstance(outcome, NoSlotFree) and len(outcome.alternatives) == 3
    assert len(calendar.event_reads) == 1


def test_a_hold_of_another_thread_is_busy_and_the_threads_own_hold_is_free() -> None:
    slot = Interval(at(10, 15), at(10, 15, 30))
    other = Hold(thread_id="other-thread", approval_id="a-1", interval=slot)
    own = Hold(thread_id="this-thread", approval_id="a-2", interval=slot)

    held_elsewhere = plan_meeting(request(meeting_at(TUESDAY_3PM), FakeCalendar(), holds=(other,)))
    held_here = plan_meeting(request(meeting_at(TUESDAY_3PM), FakeCalendar(), holds=(own,)))

    assert isinstance(held_elsewhere, NoSlotFree)
    assert held_here == SlotFree(slot=slot, outside_work_hours=False)


def test_a_calendar_full_for_a_week_offers_no_alternatives() -> None:
    calendar = FakeCalendar()
    calendar.add(event(at(9, 0), at(16, 0)))

    outcome = plan_meeting(request(meeting_at(TUESDAY_3PM), calendar))

    assert outcome == NoSlotFree(alternatives=())
