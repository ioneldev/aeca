"""An action's args and the email's facts are all it takes to write the message it sends."""

import json
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from support.facts import make_facts

from aeca.domain.actions import Action, Verb
from aeca.domain.approval import Approval, ApprovalState
from aeca.domain.approval_messages import help_action, request_action
from aeca.domain.compose import ComposeRequest, compose_message
from aeca.domain.outgoing import OutgoingMessage
from aeca.domain.templates import (
    ReplyRequest,
    acknowledgement,
    decline,
    describe_slot,
    help_reply,
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


@pytest.mark.parametrize(
    ("template", "approved", "kind"),
    [
        ("decline", True, "approved-decline"),
        ("acknowledgement", True, "approved-acknowledgement"),
        ("decline", False, "decline"),
    ],
)
def test_an_approved_automatic_kind_is_marked_approved(
    template: str, approved: bool, kind: str
) -> None:
    action = Action(verb=Verb.SEND_REPLY, key="k-1", args={"template": template})

    message = compose_message(
        ComposeRequest(action=action, facts=FACTS, manager_address=MANAGER, approved=approved)
    )

    assert message.kind == kind


def test_an_approved_kind_that_is_already_distinct_is_kept() -> None:
    action = Action(
        verb=Verb.SEND_REPLY, key="k-1", args={"template": "confirmation", **slot_args(SLOT)}
    )

    message = compose_message(
        ComposeRequest(action=action, facts=FACTS, manager_address=MANAGER, approved=True)
    )

    assert message.kind == "confirmation"


# --- the approval request and the help reply (spec §4.5) ---

EVENT = Action(verb=Verb.CREATE_EVENT, key="k-event@0", args={**slot_args(SLOT), "title": "Sync"})
CONFIRMATION = Action(
    verb=Verb.SEND_REPLY, key="k-confirm@0", args={"template": "confirmation", **slot_args(SLOT)}
)
URGENT_DRAFT = Action(
    verb=Verb.SEND_REPLY,
    key="k-urgent@0",
    args={"template": "urgent_reply", "text": "Approve the budget today.\nCalling you."},
)


def approval_with(*actions: Action) -> Approval:
    return Approval(
        id="a-42",
        source_message_id="m1",
        source_thread_id="t1",
        attempt=0,
        actions=actions,
        state=ApprovalState.AWAITING,
        request_thread_id=None,
        summary="Wants a sync on Tuesday.",
        holds=(SLOT,),
    )


def requested(approval: Approval, *, outside_work_hours: bool = False) -> OutgoingMessage:
    action = request_action(approval, to=MANAGER, outside_work_hours=outside_work_hours)
    return compose_message(
        ComposeRequest(action=action, facts=FACTS, manager_address="someone-else@example.com")
    )


def test_the_request_goes_where_it_was_opened_for_under_the_approval_id() -> None:
    message = requested(approval_with(EVENT, CONFIRMATION))

    assert (message.to, message.kind, message.aeca_id, message.thread_id) == (
        MANAGER,
        "approval-request",
        "a-42",
        None,
    )
    assert message.subject.startswith("[AECA] Approve: ")


def test_the_request_key_is_the_approval_id() -> None:
    assert request_action(approval_with(EVENT), to=MANAGER, outside_work_hours=False).key == "a-42"


def test_the_request_describes_the_event_and_quotes_the_confirmation_it_would_send() -> None:
    body = requested(approval_with(EVENT, CONFIRMATION)).body

    assert f'create the calendar event "Sync" on {describe_slot(SLOT, BUCHAREST)}' in body
    assert "| Thank you for your request. The meeting is confirmed for:" in body
    assert "Wants a sync on Tuesday." in body


def test_the_request_of_an_urgent_draft_carries_the_notice_and_the_prefixed_draft() -> None:
    body = requested(approval_with(URGENT_DRAFT)).body

    assert "looks urgent" in body
    assert "| | Approve the budget today.\n| Calling you." in body


def test_the_request_notes_a_slot_outside_working_hours() -> None:
    assert "outside working hours" in requested(approval_with(EVENT), outside_work_hours=True).body
    assert "outside working hours" not in requested(approval_with(EVENT)).body


def test_a_resume_writes_the_same_request() -> None:
    approval = approval_with(EVENT, CONFIRMATION, URGENT_DRAFT)

    assert requested(approval) == requested(approval)


def test_the_help_action_is_one_per_approval_and_writes_the_help_reply() -> None:
    approval = approval_with(EVENT)
    action = help_action(approval)

    message = compose_message(ComposeRequest(action=action, facts=FACTS, manager_address=MANAGER))

    assert help_action(approval) == action
    assert help_action(replace(approval, attempt=1)).key != action.key
    assert message == help_reply(ReplyRequest(facts=FACTS, aeca_id=action.key))
