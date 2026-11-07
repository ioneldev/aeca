"""Until the approval service exists, an approval is saved and waits: nothing is sent, and a second
open for the same email changes nothing."""

from dataclasses import replace
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from fakes.recording_tracer import RecordingTracer
from support.facts import make_facts

from aeca.adapters.sqlite_state import SqliteState
from aeca.application.deferred_approval import DeferredApprovalOpener
from aeca.application.ports import OpenApproval
from aeca.domain.actions import Action, ActionStatus, Verb
from aeca.domain.approval import ApprovalState
from aeca.domain.ids import approval_id
from aeca.domain.time_range import Interval

BUCHAREST = ZoneInfo("Europe/Bucharest")
SLOT = Interval(
    datetime(2026, 11, 10, 15, 0, tzinfo=BUCHAREST),
    datetime(2026, 11, 10, 15, 30, tzinfo=BUCHAREST),
)
NOON_BEFORE = datetime(2026, 11, 9, 12, 0, tzinfo=BUCHAREST)
REPLY = Action(verb=Verb.SEND_REPLY, key="k-reply@0", args={"template": "confirmation"})


@pytest.fixture
def store(tmp_path: Path) -> SqliteState:
    return SqliteState(tmp_path / "state.db")


def opening(**overrides: object) -> OpenApproval:
    request = OpenApproval(
        source=make_facts(message_id="m1", thread_id="t1"),
        actions=(REPLY,),
        holds=(SLOT,),
        summary="Wants a sync on Tuesday.",
    )
    return replace(request, **overrides)  # type: ignore[arg-type]


def test_it_saves_an_awaiting_approval_with_its_actions_and_holds(store: SqliteState) -> None:
    tracer = RecordingTracer()

    opened_id = DeferredApprovalOpener(store, tracer).open(opening())

    approval = store.approval_get(opened_id)
    assert opened_id == approval_id("m1", 0)
    assert approval is not None
    assert (approval.state, approval.actions, approval.holds) == (
        ApprovalState.AWAITING, (REPLY,), (SLOT,),
    )  # fmt: skip
    assert (approval.source_thread_id, approval.request_thread_id) == ("t1", None)
    assert approval.summary == "Wants a sync on Tuesday."
    [traced] = tracer.of_kind("approval_request_deferred")
    assert traced.fields["approval_id"] == opened_id


def test_its_actions_are_registered_under_the_approval_and_never_resumed(
    store: SqliteState,
) -> None:
    DeferredApprovalOpener(store, RecordingTracer()).open(opening())

    record = store.action_get(REPLY.key)
    assert record is not None
    assert (record.approval_id, record.status) == (approval_id("m1", 0), ActionStatus.PENDING)
    assert store.actions_unfinished() == []


def test_its_holds_are_busy_for_other_threads(store: SqliteState) -> None:
    DeferredApprovalOpener(store, RecordingTracer()).open(opening())

    assert [(hold.thread_id, hold.interval) for hold in store.holds_active(NOON_BEFORE)] == [
        ("t1", SLOT)
    ]


def test_opening_again_returns_the_same_id_and_changes_nothing(store: SqliteState) -> None:
    tracer = RecordingTracer()
    opener = DeferredApprovalOpener(store, tracer)
    first = opener.open(opening())
    approval = store.approval_get(first)
    assert approval is not None
    store.approval_save(replace(approval, state=ApprovalState.APPROVED))

    second = opener.open(opening(summary="A different summary"))

    assert second == first
    reread = store.approval_get(first)
    assert reread is not None
    assert (reread.state, reread.summary) == (ApprovalState.APPROVED, "Wants a sync on Tuesday.")
    assert len(tracer.of_kind("approval_request_deferred")) == 1


def test_the_attempt_names_the_approval(store: SqliteState) -> None:
    opened_id = DeferredApprovalOpener(store, RecordingTracer()).open(
        opening(attempt=1, actions=(replace(REPLY, key="k-reply@1"),))
    )

    assert opened_id == approval_id("m1", 1)
