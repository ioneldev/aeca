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
from aeca.domain.replies import AUTOMATED_SENDER, REFUSED_EVEN_WITH_APPROVAL
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
    return isinstance(context.meeting, SlotFree)


def _alternatives_available(context: PolicyContext) -> bool:
    return isinstance(context.meeting, NoSlotFree) and bool(context.meeting.alternatives)


def _urgent_draft_survives(context: PolicyContext) -> bool:
    return (
        context.decision.urgent_draft is not None
        and context.facts.internal
        and context.reply_block not in REFUSED_EVEN_WITH_APPROVAL
        and context.reply_block != AUTOMATED_SENDER
    )


def _urgent_notice_needed(context: PolicyContext) -> bool:
    return context.facts.internal and not _urgent_draft_survives(context)


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
        args = self._args(step)
        discriminator = args.get("label") or args.get("template") or ""
        name = f"{step.verb.value}:{discriminator}" if discriminator else step.verb.value
        if gated:
            name = f"{name}@{self.context.attempt}"
        return Action(
            verb=step.verb, key=action_key(self.context.facts.message_id, name), args=args
        )

    def _args(self, step: Step) -> dict[str, str]:
        if step.verb is Verb.APPLY_LABEL:
            return {"label": step.label or self.context.decision.label.value}
        if step.verb is Verb.SEND_REPLY:
            return self._reply_args()
        if step.verb is Verb.CREATE_EVENT:
            return self._event_args()
        return {}

    def _reply_args(self) -> dict[str, str]:
        decision = self.context.decision
        if self.is_urgent_record:
            return {"template": "urgent_reply", "text": visible(decision.urgent_draft or "")}
        outcome = self.context.meeting
        if isinstance(outcome, SlotFree):
            return {"template": "confirmation", **_slot_args(outcome.slot)}
        if isinstance(outcome, NoSlotFree):
            return {"template": "alternatives", "slots": _slots_json(outcome.alternatives)}
        if decision.label is Label.SALES_OUTREACH:
            return {"template": ReplyKind.DECLINE.value}
        return {"template": decision.reply.value}

    def _event_args(self) -> dict[str, str]:
        outcome = self.context.meeting
        meeting = self.context.decision.meeting
        assert isinstance(outcome, SlotFree)
        title = meeting.title if meeting is not None else self.context.facts.subject
        return {**_slot_args(outcome.slot), "title": title}


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
