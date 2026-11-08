"""Writes the message a sending action sends, from the action's args and the email's facts alone,
so a resume writes the same message the first run would have."""

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import datetime
from zoneinfo import ZoneInfo

from aeca.domain.actions import Action, Verb
from aeca.domain.approval_messages import HELP_TEMPLATE, RequestArgs
from aeca.domain.facts import EmailFacts
from aeca.domain.outgoing import OutgoingMessage
from aeca.domain.templates import (
    APPROVED_PREFIX,
    AUTOMATIC_REPLY_KINDS,
    ApprovalRequest,
    ReplyRequest,
    RequestStep,
    UrgentNotice,
    acknowledgement,
    alternatives,
    approval_request,
    confirmation,
    decline,
    event_headline,
    event_step,
    help_reply,
    reply_headline,
    reply_step,
    thank_you,
    urgent_notice,
    urgent_reply,
)
from aeca.domain.time_range import Interval


@dataclass(frozen=True, slots=True)
class ComposeRequest:
    action: Action
    facts: EmailFacts
    manager_address: str
    # True when the manager approved the action: its reply must not count as an unapproved one.
    approved: bool = False


def compose_message(request: ComposeRequest) -> OutgoingMessage:
    action = request.action
    if action.verb is Verb.NOTIFY_MANAGER:
        return _urgent_notice(request)
    if action.verb is Verb.REQUEST_APPROVAL:
        return _approval_request(request)
    if action.verb is Verb.SEND_REPLY:
        reply = _reply(action, ReplyRequest(facts=request.facts, aeca_id=action.key))
        return _marked_approved(reply) if request.approved else reply
    raise ValueError(f"action {action.key} ({action.verb.value}) sends no message")


def _marked_approved(reply: OutgoingMessage) -> OutgoingMessage:
    if reply.kind not in AUTOMATIC_REPLY_KINDS:
        return reply
    return replace(reply, kind=f"{APPROVED_PREFIX}{reply.kind}")


def _reply(action: Action, request: ReplyRequest) -> OutgoingMessage:
    template = action.args.get("template", "")
    write = REPLY_WRITERS.get(template)
    if write is None:
        raise ValueError(f"action {action.key} names no known reply template: {template!r}")
    return write(action.args, request)


def _urgent_notice(request: ComposeRequest) -> OutgoingMessage:
    args = request.action.args
    return urgent_notice(
        UrgentNotice(
            facts=request.facts,
            aeca_id=request.action.key,
            manager_address=request.manager_address,
            summary=args["summary"],
            quote=args["quote"],
        )
    )


def _approval_request(request: ComposeRequest) -> OutgoingMessage:
    args = RequestArgs.of(request.action)
    return approval_request(
        ApprovalRequest(
            facts=request.facts,
            approval_id=request.action.key,
            manager_address=args.to,
            summary=args.summary,
            headline=_headline(args.actions, request.facts),
            steps=tuple(_request_step(action, request) for action in args.actions),
            outside_work_hours=args.outside_work_hours,
            urgent=any(_carries_urgent_notice(action) for action in args.actions),
            urgent_quote=_urgent_quote(args.actions),
        )
    )


# A step that sends a reply quotes that very reply, as approving it would send it.
def _request_step(action: Action, request: ComposeRequest) -> RequestStep:
    if action.verb is Verb.CREATE_EVENT:
        args = action.args
        return RequestStep(event_step(args["title"], _slot(args), ZoneInfo(args["zone"])))
    if action.verb is Verb.SEND_REPLY:
        reply = compose_message(replace(request, action=action, approved=True))
        return RequestStep(reply_step(request.facts), draft=reply.body)
    raise ValueError(f"an approval cannot ask for {action.verb.value} ({action.key})")


def _headline(actions: tuple[Action, ...], facts: EmailFacts) -> str:
    if not actions:
        return f'the email "{facts.subject}"'
    first = actions[0]
    if first.verb is Verb.CREATE_EVENT:
        args = first.args
        return event_headline(args["title"], _slot(args), ZoneInfo(args["zone"]))
    return reply_headline(first.args.get("template", ""), facts)


def _urgent_quote(actions: tuple[Action, ...]) -> str:
    return next(
        (action.args.get("quote", "") for action in actions if _carries_urgent_notice(action)), ""
    )


def _carries_urgent_notice(action: Action) -> bool:
    """An urgent draft always does; an urgent meeting's actions do through their quote. An urgent
    draft recorded before the quote was kept has the template only."""
    return "quote" in action.args or action.args.get("template") == "urgent_reply"


def _confirmation(args: Mapping[str, str], request: ReplyRequest) -> OutgoingMessage:
    return confirmation(request, slot=_slot(args), zone=ZoneInfo(args["zone"]))


def _alternatives(args: Mapping[str, str], request: ReplyRequest) -> OutgoingMessage:
    offered: list[dict[str, str]] = json.loads(args["slots"])
    if not offered:
        raise ValueError(f"the alternatives reply {request.aeca_id} offers no slot")
    slots = tuple(_slot(slot) for slot in offered)
    return alternatives(request, slots=slots, zone=ZoneInfo(offered[0]["zone"]))


def _urgent_reply(args: Mapping[str, str], request: ReplyRequest) -> OutgoingMessage:
    return urgent_reply(request, draft=args["text"])


def _slot(args: Mapping[str, str]) -> Interval:
    return Interval(datetime.fromisoformat(args["start"]), datetime.fromisoformat(args["end"]))


ReplyWriter = Callable[[Mapping[str, str], ReplyRequest], OutgoingMessage]

REPLY_WRITERS: Mapping[str, ReplyWriter] = {
    "acknowledgement": lambda _args, request: acknowledgement(request),
    "thank_you": lambda _args, request: thank_you(request),
    "decline": lambda _args, request: decline(request),
    "confirmation": _confirmation,
    "alternatives": _alternatives,
    "urgent_reply": _urgent_reply,
    HELP_TEMPLATE: lambda _args, request: help_reply(request),
}
