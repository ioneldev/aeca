"""The state store is what makes every action run once, so these tests read it back from disk."""

import sqlite3
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from aeca.adapters.sqlite_state import SqliteState
from aeca.domain.actions import Action, ActionStatus, Verb
from aeca.domain.approval import Approval, ApprovalState, Hold
from aeca.domain.errors import AecaError
from aeca.domain.journal import JournalEntry
from aeca.domain.time_range import Interval

NOON = datetime(2026, 11, 9, 12, 0, tzinfo=UTC)


@pytest.fixture
def state(tmp_path: Path) -> SqliteState:
    return SqliteState(tmp_path / "state.db")


def label_action(key: str = "m1:label") -> Action:
    return Action(verb=Verb.APPLY_LABEL, key=key, args={"label": "TASK"})


def reply_action(key: str = "m1:reply") -> Action:
    return Action(verb=Verb.SEND_REPLY, key=key, args={"body": "Thanks"})


def entry_of(*actions: Action, message_id: str = "m1") -> JournalEntry:
    return JournalEntry(
        source_message_id=message_id, decision_json='{"label":"TASK"}', actions=actions
    )


def interval_starting_in(minutes: int) -> Interval:
    start = NOON + timedelta(minutes=minutes)
    return Interval(start=start, end=start + timedelta(minutes=30))


def approval_of(approval_id: str = "appr-m1", **overrides: object) -> Approval:
    fields: dict[str, object] = {
        "id": approval_id,
        "source_message_id": "m1",
        "source_thread_id": "t1",
        "attempt": 1,
        "actions": (reply_action(),),
        "state": ApprovalState.AWAITING,
        "request_thread_id": "req-t1",
        "summary": "Reply to Jürgen",
        "holds": (interval_starting_in(60),),
    }
    return Approval(**{**fields, **overrides})  # type: ignore[arg-type]


def test_journal_roundtrip(state: SqliteState) -> None:
    entry = entry_of(label_action(), reply_action())

    state.journal_record(entry)

    assert state.journal_get("m1") == entry
    assert state.journal_get("unknown") is None


def test_the_first_journal_entry_for_a_message_wins(state: SqliteState) -> None:
    first = entry_of(label_action())
    state.journal_record(first)

    state.journal_record(entry_of(reply_action()))

    assert state.journal_get("m1") == first


def test_recorded_actions_start_pending(state: SqliteState) -> None:
    state.journal_record(entry_of(label_action()))

    record = state.action_get("m1:label")

    assert record is not None
    assert record.status is ActionStatus.PENDING
    assert record.source_message_id == "m1"
    assert record.ref is None
    assert state.action_get("nope") is None


def test_unfinished_actions_listed(state: SqliteState) -> None:
    state.journal_record(entry_of(label_action("a"), label_action("b"), label_action("c")))
    state.action_update("a", ActionStatus.DONE, ref="gmail-1")
    state.action_update("b", ActionStatus.EXECUTING)
    state.action_update("c", ActionStatus.FAILED)
    state.journal_record(entry_of(label_action("d"), message_id="m2"))

    unfinished = state.actions_unfinished()

    assert [(record.action.key, record.status) for record in unfinished] == [
        ("b", ActionStatus.EXECUTING),
        ("d", ActionStatus.PENDING),
    ]


def test_updating_an_action_keeps_its_ref_unless_given_a_new_one(state: SqliteState) -> None:
    state.journal_record(entry_of(label_action("a")))
    state.action_update("a", ActionStatus.EXECUTING, ref="draft-7")

    state.action_update("a", ActionStatus.DONE)

    record = state.action_get("a")
    assert record is not None
    assert (record.status, record.ref) == (ActionStatus.DONE, "draft-7")


def test_updating_an_unknown_action_refuses(state: SqliteState) -> None:
    with pytest.raises(AecaError):
        state.action_update("ghost", ActionStatus.DONE)


def test_an_approval_roundtrips_and_registers_its_actions(state: SqliteState) -> None:
    approval = approval_of()

    state.approval_save(approval)

    assert state.approval_get("appr-m1") == approval
    assert state.approval_by_request_thread("req-t1") == approval
    record = state.action_get("m1:reply")
    assert record is not None
    assert record.approval_id == "appr-m1"
    assert record.status is ActionStatus.PENDING


def test_saving_an_approval_again_changes_its_state_not_its_actions_progress(
    state: SqliteState,
) -> None:
    state.approval_save(approval_of())
    state.action_update("m1:reply", ActionStatus.DONE, ref="sent-1")

    state.approval_save(approval_of(state=ApprovalState.EXECUTED))

    saved = state.approval_get("appr-m1")
    record = state.action_get("m1:reply")
    assert saved is not None and saved.state is ApprovalState.EXECUTED
    assert record is not None and record.status is ActionStatus.DONE


def test_only_awaiting_approvals_are_listed(state: SqliteState) -> None:
    state.approval_save(approval_of("a1"))
    state.approval_save(approval_of("a2", state=ApprovalState.REJECTED, request_thread_id="r2"))
    state.approval_save(approval_of("a3", request_thread_id="r3"))

    assert [approval.id for approval in state.approvals_awaiting()] == ["a1", "a3"]


def test_unknown_approvals_are_none(state: SqliteState) -> None:
    assert state.approval_get("ghost") is None
    assert state.approval_by_request_thread("ghost") is None


def test_holds_released_by_approval(state: SqliteState) -> None:
    state.holds_add(
        [
            Hold(thread_id="t1", approval_id="a1", interval=interval_starting_in(60)),
            Hold(thread_id="t2", approval_id="a2", interval=interval_starting_in(90)),
        ]
    )

    state.holds_release("a1")

    assert [hold.approval_id for hold in state.holds_active(NOON)] == ["a2"]


def test_holds_expire_at_slot_start(state: SqliteState) -> None:
    started = Hold(thread_id="t1", approval_id="a1", interval=interval_starting_in(-1))
    starting_now = Hold(thread_id="t1", approval_id="a2", interval=interval_starting_in(0))
    ahead = Hold(thread_id="t1", approval_id="a3", interval=interval_starting_in(1))
    state.holds_add([started, starting_now, ahead])

    assert state.holds_active(NOON) == [ahead]


def test_an_incident_opens_once_and_reopens_after_close(state: SqliteState) -> None:
    assert state.incident_open("gmail_down") is True
    assert state.incident_open("gmail_down") is False
    assert state.incident_open("budget") is True

    state.incident_close("gmail_down")

    assert state.incident_open("gmail_down") is True


def test_closing_an_incident_that_is_not_open_is_harmless(state: SqliteState) -> None:
    state.incident_close("never_opened")


def test_spend_totals_by_day(state: SqliteState) -> None:
    monday, tuesday = date(2026, 11, 9), date(2026, 11, 10)

    assert state.spend_add(monday, Decimal("0.10")) == Decimal("0.10")
    assert state.spend_add(monday, Decimal("0.20")) == Decimal("0.30")
    state.spend_add(tuesday, Decimal("1.5"))

    assert state.spend_on(monday) == Decimal("0.30")
    assert state.spend_on(tuesday) == Decimal("1.5")
    assert state.spend_on(date(2026, 11, 11)) == Decimal(0)


def test_attempts_increment(state: SqliteState) -> None:
    assert [state.attempts_increment("m1") for _ in range(3)] == [1, 2, 3]
    assert state.attempts_increment("m2") == 1


def test_answers_are_seen_once_marked(state: SqliteState) -> None:
    assert state.answer_seen("r1") is False

    state.answer_mark_seen("r1")
    state.answer_mark_seen("r1")

    assert state.answer_seen("r1") is True


def test_state_survives_reopening(tmp_path: Path) -> None:
    path = tmp_path / "state.db"
    first = SqliteState(path)
    first.journal_record(entry_of(label_action()))
    first.approval_save(approval_of())
    first.holds_add(
        [Hold(thread_id="t1", approval_id="appr-m1", interval=interval_starting_in(60))]
    )
    first.attempts_increment("m1")
    first.spend_add(date(2026, 11, 9), Decimal("0.07"))
    first.incident_open("gmail_down")
    first.answer_mark_seen("r1")

    second = SqliteState(path)

    assert second.journal_get("m1") == entry_of(label_action())
    assert second.approval_get("appr-m1") == approval_of()
    assert len(second.holds_active(NOON)) == 1
    assert second.attempts_increment("m1") == 2
    assert second.spend_on(date(2026, 11, 9)) == Decimal("0.07")
    assert second.incident_open("gmail_down") is False
    assert second.answer_seen("r1") is True


def user_version_of(path: Path) -> int:
    connection = sqlite3.connect(path)
    try:
        return int(connection.execute("PRAGMA user_version").fetchone()[0])
    finally:
        connection.close()


def test_schema_version_is_one_and_a_mismatch_refuses(tmp_path: Path) -> None:
    path = tmp_path / "state.db"
    SqliteState(path)
    assert user_version_of(path) == 1

    connection = sqlite3.connect(path)
    connection.execute("PRAGMA user_version = 2")
    connection.commit()
    connection.close()

    with pytest.raises(AecaError) as refusal:
        SqliteState(path)

    assert "2" in str(refusal.value)
    assert "back up" in refusal.value.next_action.lower()
    assert user_version_of(path) == 2


def test_a_state_in_memory_works_for_tests(tmp_path: Path) -> None:
    state = SqliteState(Path(":memory:"))

    assert state.attempts_increment("m1") == 1
