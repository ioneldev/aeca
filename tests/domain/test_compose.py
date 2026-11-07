"""An action's args and the email's facts are all it takes to write the message it sends."""

import json
from collections.abc import Callable
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from support.facts import make_facts

from aeca.domain.actions import Action, Verb
from aeca.domain.compose import ComposeRequest, compose_message
from aeca.domain.outgoing import OutgoingMessage
from aeca.domain.templates import (
    ReplyRequest,
    acknowledgement,
    decline,
    describe_slot,
    thank_you,
)
from aeca.domain.time_range import Interval

BUCHAREST = ZoneInfo("Europe/Bucharest")
SLOT = Interval(
    datetime(2026, 11, 10, 15, 0, tzinfo=BUCHAREST),
    datetime(2026, 11, 10, 15, 30, tzinfo=BUCHAREST),
)
SECOND_SLOT = Interval(
    datetime(2026, 11, 11, 9, 0, tzinfo=BUCHAREST), datetime(2026, 11, 11, 9, 30, tzinfo=BUCHAREST)
)
FACTS = make_facts(sender="client@example.com", thread_id="t1")
MANAGER = "boss@example.com"


def slot_args(slot: Interval) -> dict[str, str]:
    return {"start": slot.start.isoformat(), "end": slot.end.isoformat(), "zone": BUCHAREST.key}


def composed(verb: Verb, **args: str) -> str:
    action = Action(verb=verb, key="k-1", args=args)
    return compose_message(ComposeRequest(action=action, facts=FACTS, manager_address=MANAGER)).body


@pytest.mark.parametrize(
    ("template", "write"),
    [("acknowledgement", acknowledgement), ("thank_you", thank_you), ("decline", decline)],
)
def test_a_reply_template_is_written_from_its_name(
    template: str, write: Callable[[ReplyRequest], OutgoingMessage]
) -> None:
    action = Action(verb=Verb.SEND_REPLY, key="k-1", args={"template": template})

    message = compose_message(ComposeRequest(action=action, facts=FACTS, manager_address=MANAGER))

    assert message == write(ReplyRequest(facts=FACTS, aeca_id="k-1"))


def test_a_confirmation_states_the_slot_in_its_zone() -> None:
    body = composed(Verb.SEND_REPLY, template="confirmation", **slot_args(SLOT))

    assert describe_slot(SLOT, BUCHAREST) in body


def test_alternatives_list_every_offered_slot() -> None:
    slots = json.dumps([slot_args(SLOT), slot_args(SECOND_SLOT)])

    body = composed(Verb.SEND_REPLY, template="alternatives", slots=slots)

    assert describe_slot(SLOT, BUCHAREST) in body
    assert describe_slot(SECOND_SLOT, BUCHAREST) in body


def test_the_urgent_reply_carries_the_draft() -> None:
    body = composed(Verb.SEND_REPLY, template="urgent_reply", text="On it, calling you at noon.")

    assert "On it, calling you at noon." in body


def test_the_urgent_notice_goes_to_the_manager_with_the_summary_and_quote() -> None:
    action = Action(
        verb=Verb.NOTIFY_MANAGER, key="k-1", args={"summary": "Needs it.", "quote": "today"}
    )

    message = compose_message(ComposeRequest(action=action, facts=FACTS, manager_address=MANAGER))

    assert (message.to, message.kind, message.aeca_id) == (MANAGER, "urgent-notice", "k-1")
    assert "Needs it." in message.body and "today" in message.body


def test_every_reply_goes_to_the_original_sender_in_its_thread() -> None:
    action = Action(verb=Verb.SEND_REPLY, key="k-1", args={"template": "acknowledgement"})

    message = compose_message(ComposeRequest(action=action, facts=FACTS, manager_address=MANAGER))

    assert (message.to, message.thread_id) == ("client@example.com", "t1")


@pytest.mark.parametrize(
    "action",
    [
        Action(verb=Verb.SEND_REPLY, key="k-1", args={"template": "poem"}),
        Action(verb=Verb.ARCHIVE, key="k-1", args={}),
    ],
    ids=["unknown-template", "not-a-send"],
)
def test_an_action_with_no_message_is_refused(action: Action) -> None:
    with pytest.raises(ValueError, match="k-1"):
        compose_message(ComposeRequest(action=action, facts=FACTS, manager_address=MANAGER))
