"""The policy: each label is a record of ordered steps, and URGENT is a second record of the same
shape. `plan_actions` turns a validated decision and the email's facts into the actions that run
at once and the actions that wait for the manager. Code owns it; the model never does."""

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime

from aeca.domain.actions import Action, Verb
from aeca.domain.decision import Decision
from aeca.domain.facts import EmailFacts
from aeca.domain.ids import action_key
from aeca.domain.labels import FINANCE_LABEL, URGENT_LABEL, Label, ReplyKind
from aeca.domain.meeting_outcome import MeetingOutcome, NoSlotFree, SlotFree
from aeca.domain.replies import AUTOMATED_SENDER, DMARC_FAILED, REFUSED_EVEN_WITH_APPROVAL
from aeca.domain.text import visible
from aeca.domain.time_range import Interval

MAX_ALTERNATIVES = 3
INBOX_REMOVERS = frozenset({Verb.ARCHIVE, Verb.MOVE_TO_SPAM})
NO_APPROVAL = "none"
APPROVAL_REQUIRED = "required"
SETTING_PREFIX = "setting:"


@dataclass(frozen=True, slots=True)
class PolicySettings:
    sales_decline_needs_approval: bool


@dataclass(frozen=True, slots=True)
class PolicyContext:
    decision: Decision
    facts: EmailFacts
    meeting: MeetingOutcome | None
    settings: PolicySettings
    reply_block: str | None
    attempt: int = 0


@dataclass(frozen=True, slots=True)
class Step:
    when: str
    verb: Verb
    approval: str
    label: str | None = None


@dataclass(frozen=True, slots=True)
class ActionPlan:
    automatic: tuple[Action, ...]
    for_approval: tuple[Action, ...]
    holds: tuple[Interval, ...]


def _always(context: PolicyContext) -> bool:
    return True


def _reply_warranted(context: PolicyContext) -> bool:
    return context.decision.reply is not ReplyKind.NONE


def _reply_warranted_without_invoice(context: PolicyContext) -> bool:
    return _reply_warranted(context) and not context.decision.finance


def _is_invoice(context: PolicyContext) -> bool:
    return context.decision.finance


def _slot_free(context: PolicyContext) -> bool:
    return isinstance(context.meeting, SlotFree) and _sender_may_book(context)


def _alternatives_available(context: PolicyContext) -> bool:
    outcome = context.meeting
    return (
        isinstance(outcome, NoSlotFree) and bool(outcome.alternatives) and _sender_may_book(context)
    )


def _sender_may_book(context: PolicyContext) -> bool:
    """A forged sender must not get an event onto the calendar through an approval request."""
    return context.reply_block != DMARC_FAILED


def _urgent_draft_survives(context: PolicyContext) -> bool:
    return (
        context.decision.urgent_draft is not None
        and context.decision.label is not Label.MEETING_REQUEST
        and context.facts.internal
        and context.reply_block not in REFUSED_EVEN_WITH_APPROVAL
        and context.reply_block != AUTOMATED_SENDER
    )


def _meeting_approval_opens(context: PolicyContext) -> bool:
    if _slot_free(context):
        return True
    reply_refused = context.reply_block in REFUSED_EVEN_WITH_APPROVAL
    return _alternatives_available(context) and not reply_refused


def _urgent_notice_rides_in_meeting_approval(context: PolicyContext) -> bool:
    decision = context.decision
    return (
        decision.urgent
        and context.facts.internal
        and decision.label is Label.MEETING_REQUEST
        and _meeting_approval_opens(context)
    )


def _urgent_notice_needed(context: PolicyContext) -> bool:
    """The manager hears of an urgent internal email once: in the approval request when one is
    opened for it, otherwise in a notice of its own."""
    return (
        context.facts.internal
        and not _urgent_draft_survives(context)
        and not _urgent_notice_rides_in_meeting_approval(context)
    )


PREDICATES: Mapping[str, Callable[[PolicyContext], bool]] = {
    "always": _always,
    "reply_warranted": _reply_warranted,
    "reply_warranted_without_invoice": _reply_warranted_without_invoice,
    "is_invoice": _is_invoice,
    "slot_free": _slot_free,
    "alternatives_available": _alternatives_available,
    "urgent_draft_survives": _urgent_draft_survives,
    "urgent_notice_needed": _urgent_notice_needed,
}

SALES_DECLINE_APPROVAL = "setting:SALES_DECLINE_NEEDS_APPROVAL"

POLICY_STEPS: Mapping[Label, tuple[Step, ...]] = {
    Label.MARKETING: (
        Step("always", Verb.APPLY_LABEL, NO_APPROVAL),
        Step("reply_warranted", Verb.SEND_REPLY, NO_APPROVAL),
        Step("always", Verb.ARCHIVE, NO_APPROVAL),
    ),
    Label.MEETING_REQUEST: (
        Step("always", Verb.APPLY_LABEL, NO_APPROVAL),
        Step("slot_free", Verb.CREATE_EVENT, APPROVAL_REQUIRED),
        Step("slot_free", Verb.SEND_REPLY, APPROVAL_REQUIRED),
        Step("alternatives_available", Verb.SEND_REPLY, APPROVAL_REQUIRED),
    ),
    Label.SALES_OUTREACH: (
        Step("always", Verb.APPLY_LABEL, NO_APPROVAL),
        Step("always", Verb.SEND_REPLY, SALES_DECLINE_APPROVAL),
        Step("always", Verb.ARCHIVE, NO_APPROVAL),
    ),
    Label.TASK: (
        Step("always", Verb.APPLY_LABEL, NO_APPROVAL),
        Step("reply_warranted", Verb.SEND_REPLY, NO_APPROVAL),
    ),
    Label.INFO_ONLY: (
        Step("always", Verb.APPLY_LABEL, NO_APPROVAL),
        Step("is_invoice", Verb.APPLY_LABEL, NO_APPROVAL, label=FINANCE_LABEL),
        Step("reply_warranted_without_invoice", Verb.SEND_REPLY, NO_APPROVAL),
        Step("is_invoice", Verb.ARCHIVE, NO_APPROVAL),
    ),
    # Spike 3 (is a user label named SPAM reserved?) has not run; if it isn't, adding
    # Step("always", Verb.APPLY_LABEL, NO_APPROVAL) here is the whole change.
    Label.SPAM: (Step("always", Verb.MOVE_TO_SPAM, NO_APPROVAL),),
    Label.UNKNOWN: (Step("always", Verb.APPLY_LABEL, NO_APPROVAL),),
}

URGENT_STEPS: tuple[Step, ...] = (
    Step("always", Verb.STAR, NO_APPROVAL),
    Step("always", Verb.APPLY_LABEL, NO_APPROVAL, label=URGENT_LABEL),
    Step("urgent_notice_needed", Verb.NOTIFY_MANAGER, NO_APPROVAL),
    Step("urgent_draft_survives", Verb.SEND_REPLY, APPROVAL_REQUIRED),
)


def plan_actions(context: PolicyContext) -> ActionPlan:
    primary = _Planner(context, POLICY_STEPS[context.decision.label], is_urgent_record=False)
    primary_plan = primary.plan()
    urgent_plan = _urgent_plan(context)
    automatic = _inbox_removers_last(primary_plan.automatic + urgent_plan.automatic)
    return ActionPlan(
        automatic=automatic,
        for_approval=primary_plan.for_approval + urgent_plan.for_approval,
        holds=_holds(context) if primary_plan.for_approval else (),
    )


def _urgent_plan(context: PolicyContext) -> "_PlannedSteps":
    if not context.decision.urgent or context.decision.label is Label.SPAM:
        return _PlannedSteps((), ())
    return _Planner(context, URGENT_STEPS, is_urgent_record=True).plan()


def _inbox_removers_last(actions: tuple[Action, ...]) -> tuple[Action, ...]:
    stays = tuple(action for action in actions if action.verb not in INBOX_REMOVERS)
    leaves = tuple(action for action in actions if action.verb in INBOX_REMOVERS)
    return stays + leaves


def _holds(context: PolicyContext) -> tuple[Interval, ...]:
    outcome = context.meeting
    if isinstance(outcome, SlotFree):
        return (outcome.slot,)
    if isinstance(outcome, NoSlotFree):
        return outcome.alternatives[:MAX_ALTERNATIVES]
    return ()


@dataclass(frozen=True, slots=True)
class _PlannedSteps:
    automatic: tuple[Action, ...]
    for_approval: tuple[Action, ...]


@dataclass(frozen=True, slots=True)
class _Planner:
    context: PolicyContext
    steps: tuple[Step, ...]
    is_urgent_record: bool

    def plan(self) -> _PlannedSteps:
        automatic: list[Action] = []
        for_approval: list[Action] = []
        for step in self.steps:
            if not PREDICATES[step.when](self.context):
                continue
            gated = self._needs_approval(step)
            if step.verb is Verb.SEND_REPLY and self._reply_is_refused(gated=gated):
                continue
            action = self._action(step, gated=gated)
            (for_approval if gated else automatic).append(action)
        return _PlannedSteps(tuple(automatic), tuple(for_approval))

    def _needs_approval(self, step: Step) -> bool:
        if step.approval == NO_APPROVAL:
            return False
        if step.approval == APPROVAL_REQUIRED:
            return True
        setting = step.approval.removeprefix(SETTING_PREFIX).lower()
        return bool(getattr(self.context.settings, setting))

    def _reply_is_refused(self, *, gated: bool) -> bool:
        block = self.context.reply_block
        if block is None:
            return False
        return not gated or block in REFUSED_EVEN_WITH_APPROVAL

    def _action(self, step: Step, *, gated: bool) -> Action:
        return _keyed(
            _Unkeyed(verb=step.verb, args={**self._args(step), **self._riding_notice(gated=gated)}),
            source_message_id=self.context.facts.message_id,
            attempt=self.context.attempt if gated else None,
        )

    def _riding_notice(self, *, gated: bool) -> dict[str, str]:
        if not (gated and self._rides_with_meeting_approval()):
            return {}
        return {"quote": self.context.decision.urgent_quote or ""}

    def _rides_with_meeting_approval(self) -> bool:
        return not self.is_urgent_record and _urgent_notice_rides_in_meeting_approval(self.context)

    def _args(self, step: Step) -> dict[str, str]:
        if step.verb is Verb.APPLY_LABEL:
            return {"label": step.label or self.context.decision.label.value}
        if step.verb is Verb.SEND_REPLY:
            return self._reply_args()
        if step.verb is Verb.CREATE_EVENT:
            return self._event_args()
        if step.verb is Verb.NOTIFY_MANAGER:
            return self._notice_args()
        return {}

    def _notice_args(self) -> dict[str, str]:
        decision = self.context.decision
        return {"summary": decision.summary, "quote": decision.urgent_quote or ""}

    def _reply_args(self) -> dict[str, str]:
        decision = self.context.decision
        if self.is_urgent_record:
            return {
                "template": "urgent_reply",
                "text": visible(decision.urgent_draft or ""),
                # Why it is urgent, for the approval request (spec §4.5).
                "quote": decision.urgent_quote or "",
            }
        outcome = self.context.meeting
        if isinstance(outcome, SlotFree):
            return {"template": "confirmation", **_slot_args(outcome.slot)}
        if isinstance(outcome, NoSlotFree):
            return _alternatives_args(outcome.alternatives)
        if decision.label is Label.SALES_OUTREACH:
            return {"template": ReplyKind.DECLINE.value}
        return {"template": decision.reply.value}

    def _event_args(self) -> dict[str, str]:
        outcome = self.context.meeting
        meeting = self.context.decision.meeting
        if not isinstance(outcome, SlotFree):
            raise ValueError("create_event needs a free slot")
        title = meeting.title if meeting is not None else self.context.facts.subject
        windows = meeting.windows if meeting is not None else ()
        # The times as the requester wrote them, for the event's description (spec §4.4).
        quoted = "; ".join(f'"{window.quote}"' for window in windows)
        return {**_slot_args(outcome.slot), "title": title, "quoted": quoted}


def alternatives_reply(
    source_message_id: str,
    alternatives: tuple[Interval, ...],
    *,
    attempt: int,
    urgent_quote: str | None = None,
) -> Action:
    """The gated reply offering free slots, exactly as the plan writes it for that attempt. An
    urgent meeting's carries the quote, so its next request still tells the manager why."""
    notice = {} if urgent_quote is None else {"quote": urgent_quote}
    return _keyed(
        _Unkeyed(verb=Verb.SEND_REPLY, args={**_alternatives_args(alternatives), **notice}),
        source_message_id=source_message_id,
        attempt=attempt,
    )


def for_attempt(action: Action, *, source_message_id: str, attempt: int) -> Action:
    """A gated action keyed for another attempt: a key names one action forever, so a superseded
    approval's actions are never run again under the next approval."""
    return _keyed(
        _Unkeyed(verb=action.verb, args=action.args),
        source_message_id=source_message_id,
        attempt=attempt,
    )


def is_bound_to_the_slot(action: Action) -> bool:
    """The event and its confirmation stand or fall with the slot; nothing else does."""
    return action.verb is Verb.CREATE_EVENT or action.args.get("template") == "confirmation"


@dataclass(frozen=True, slots=True)
class _Unkeyed:
    verb: Verb
    args: Mapping[str, str]


# Gated keys carry the attempt, so a superseded approval's next attempt gets new ones.
def _keyed(unkeyed: _Unkeyed, *, source_message_id: str, attempt: int | None) -> Action:
    discriminator = unkeyed.args.get("label") or unkeyed.args.get("template") or ""
    name = f"{unkeyed.verb.value}:{discriminator}" if discriminator else unkeyed.verb.value
    if attempt is not None:
        name = f"{name}@{attempt}"
    return Action(
        verb=unkeyed.verb, key=action_key(source_message_id, name), args=dict(unkeyed.args)
    )


def _alternatives_args(alternatives: tuple[Interval, ...]) -> dict[str, str]:
    return {"template": "alternatives", "slots": _slots_json(alternatives)}


def _slot_args(slot: Interval) -> dict[str, str]:
    return {
        "start": slot.start.isoformat(),
        "end": slot.end.isoformat(),
        "zone": _zone_name(slot.start),
    }


def _slots_json(slots: tuple[Interval, ...]) -> str:
    return json.dumps([_slot_args(slot) for slot in slots[:MAX_ALTERNATIVES]])


def _zone_name(moment: datetime) -> str:
    zone = moment.tzinfo
    return getattr(zone, "key", None) or moment.tzname() or "UTC"
