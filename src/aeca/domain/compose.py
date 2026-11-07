"""Writes the message a sending action sends, from the action's args and the email's facts alone,
so a resume writes the same message the first run would have."""

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import datetime
from zoneinfo import ZoneInfo

from aeca.domain.actions import Action, Verb
from aeca.domain.facts import EmailFacts
from aeca.domain.outgoing import OutgoingMessage
from aeca.domain.templates import (
    APPROVED_PREFIX,
    AUTOMATIC_REPLY_KINDS,
    ReplyRequest,
    UrgentNotice,
    acknowledgement,
    alternatives,
    confirmation,
    decline,
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
}
