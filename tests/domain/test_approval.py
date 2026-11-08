"""An approval moves awaiting → approved → executed | failed, or awaiting → rejected | superseded
(spec §4.5), and nowhere else."""

from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime

import pytest

from aeca.domain.actions import Action, Verb
from aeca.domain.approval import Approval, ApprovalState
from aeca.domain.errors import AecaError
from aeca.domain.time_range import Interval

SLOT = Interval(
    datetime(2026, 11, 10, 13, 0, tzinfo=UTC), datetime(2026, 11, 10, 13, 30, tzinfo=UTC)
)
Transition = Callable[[Approval], Approval]


def approval_in(state: ApprovalState) -> Approval:
    return Approval(
        id="a-1",
        source_message_id="m1",
        source_thread_id="t1",
        attempt=0,
        actions=(Action(verb=Verb.SEND_REPLY, key="k-1", args={"template": "confirmation"}),),
        state=state,
        request_thread_id="r1",
        summary="Wants a sync.",
        holds=(SLOT,),
    )


ALLOWED: list[tuple[ApprovalState, Transition, ApprovalState]] = [
    (ApprovalState.AWAITING, Approval.approve, ApprovalState.APPROVED),
    (ApprovalState.AWAITING, Approval.reject, ApprovalState.REJECTED),
    (ApprovalState.AWAITING, Approval.supersede, ApprovalState.SUPERSEDED),
    (ApprovalState.APPROVED, Approval.executed, ApprovalState.EXECUTED),
    (ApprovalState.APPROVED, Approval.failed, ApprovalState.FAILED),
]
TRANSITIONS: list[Transition] = [
    Approval.approve,
    Approval.reject,
    Approval.supersede,
    Approval.executed,
    Approval.failed,
]


@pytest.mark.parametrize(("start", "move", "end"), ALLOWED)
def test_each_allowed_transition_returns_a_new_approval_in_the_new_state(
    start: ApprovalState, move: Transition, end: ApprovalState
) -> None:
    before = approval_in(start)

    after = move(before)

    assert after.state is end
    assert before.state is start
    assert (after.id, after.actions, after.holds, after.request_thread_id) == (
        before.id,
        before.actions,
        before.holds,
        before.request_thread_id,
    )


def refused() -> list[tuple[ApprovalState, Transition]]:
    allowed = {(start, move) for start, move, _ in ALLOWED}
    return [
        (state, move)
        for state in ApprovalState
        for move in TRANSITIONS
        if (state, move) not in allowed
    ]


@pytest.mark.parametrize(("start", "move"), refused())
def test_every_other_transition_is_refused(start: ApprovalState, move: Transition) -> None:
    with pytest.raises(AecaError, match=start.value):
        move(approval_in(start))


def test_only_awaiting_and_approved_approvals_are_open() -> None:
    open_states = {state for state in ApprovalState if approval_in(state).is_open}

    assert open_states == {ApprovalState.AWAITING, ApprovalState.APPROVED}


ALTERNATIVES = Action(verb=Verb.SEND_REPLY, key="k-alt", args={"template": "alternatives"})


@pytest.mark.parametrize("state", list(ApprovalState))
def test_an_approval_that_offers_no_alternatives_holds_its_slots_only_while_open(
    state: ApprovalState,
) -> None:
    assert approval_in(state).holds_its_slots is approval_in(state).is_open


def test_an_executed_approval_that_offered_alternatives_keeps_holding_them() -> None:
    offered = replace(approval_in(ApprovalState.EXECUTED), actions=(ALTERNATIVES,))

    assert offered.holds_its_slots


@pytest.mark.parametrize(
    "state", [ApprovalState.REJECTED, ApprovalState.SUPERSEDED, ApprovalState.FAILED]
)
def test_an_alternatives_approval_closed_any_other_way_releases_its_slots(
    state: ApprovalState,
) -> None:
    closed = replace(approval_in(state), actions=(ALTERNATIVES,))

    assert not closed.holds_its_slots
