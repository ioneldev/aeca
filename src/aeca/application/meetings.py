"""What the calendar says about a meeting request: its invitation is already there, a proposed
time is free, or these alternatives are. The planner decides; this reads the calendar it needs."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from aeca.application.ports import Calendar
from aeca.domain.approval import Hold
from aeca.domain.calendar_event import CalendarEvent
from aeca.domain.decision import Decision, Meeting, Window
from aeca.domain.facts import EmailFacts
from aeca.domain.labels import Label
from aeca.domain.meeting_outcome import AlreadyOnCalendar, MeetingOutcome, NoSlotFree, SlotFree
from aeca.domain.planner import (
    AlternativesRequest,
    PlanSettings,
    SlotRequest,
    alternatives_search_span,
    busy_intervals,
    choose_slot,
    find_alternatives,
    meeting_duration,
)
from aeca.domain.time_range import Interval, in_utc


@dataclass(frozen=True)
class MeetingRequest:
    facts: EmailFacts
    decision: Decision
    calendar: Calendar
    holds: Sequence[Hold]
    settings: PlanSettings
    now: datetime


def plan_meeting(request: MeetingRequest) -> MeetingOutcome | None:
    """None when the decision is not a meeting request."""
    meeting = request.decision.meeting
    if request.decision.label is not Label.MEETING_REQUEST or meeting is None:
        return None
    invitation = _invitation_on_calendar(request)
    if invitation is not None:
        return AlreadyOnCalendar(event_id=invitation.id)
    duration = meeting_duration(meeting, request.settings)
    return _free_proposed_slot(request, meeting=meeting, duration=duration) or NoSlotFree(
        alternatives=_alternatives(request, duration)
    )


def _invitation_on_calendar(request: MeetingRequest) -> CalendarEvent | None:
    uid = request.facts.ical_uid
    if uid is None:
        return None
    event = request.calendar.event_by_ical_uid(uid)
    return event if event is not None and not event.cancelled else None


def _free_proposed_slot(
    request: MeetingRequest, *, meeting: Meeting, duration: timedelta
) -> SlotFree | None:
    if not meeting.windows:
        return None
    span = _windows_span(meeting.windows, duration)
    return choose_slot(
        SlotRequest(
            windows=meeting.windows,
            busy=_busy_during(span, request),
            duration=duration,
            settings=request.settings,
        )
    )


def _alternatives(request: MeetingRequest, duration: timedelta) -> tuple[Interval, ...]:
    span = alternatives_search_span(request.now, request.settings)
    return find_alternatives(
        AlternativesRequest(
            now=request.now,
            busy=_busy_during(span, request),
            duration=duration,
            settings=request.settings,
        )
    )


def _windows_span(windows: Sequence[Window], duration: timedelta) -> Interval:
    starts = [in_utc(window.start) for window in windows]
    ends = [in_utc(window.end or window.start + duration) for window in windows]
    return Interval(start=min(starts), end=max(ends))


# An event just outside the span still blocks a slot inside it through the buffer kept around it.
def _busy_during(span: Interval, request: MeetingRequest) -> tuple[Interval, ...]:
    buffer = timedelta(minutes=request.settings.buffer_minutes)
    read = Interval(start=in_utc(span.start) - buffer, end=in_utc(span.end) + buffer)
    events = request.calendar.events(read)
    return busy_intervals(events, request.holds, request.facts.thread_id)
