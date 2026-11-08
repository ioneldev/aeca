"""What the calendar said about a meeting request, as the policy needs it."""

from dataclasses import dataclass

from aeca.domain.time_range import Interval


@dataclass(frozen=True, slots=True)
class SlotFree:
    slot: Interval
    outside_work_hours: bool


@dataclass(frozen=True, slots=True)
class NoSlotFree:
    alternatives: tuple[Interval, ...]


@dataclass(frozen=True, slots=True)
class AlreadyOnCalendar:
    event_id: str


MeetingOutcome = SlotFree | NoSlotFree | AlreadyOnCalendar
