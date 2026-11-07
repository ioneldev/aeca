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
    state.approval_save(approval_of("a1", actions=(reply_action("k1"),)))
    state.approval_save(
        approval_of(
            "a2",
            actions=(reply_action("k2"),),
            state=ApprovalState.REJECTED,
            request_thread_id="r2",
        )
    )
    state.approval_save(approval_of("a3", actions=(reply_action("k3"),), request_thread_id="r3"))

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


def test_a_fresh_file_is_created_at_version_two(tmp_path: Path) -> None:
    path = tmp_path / "state.db"

    SqliteState(path)

    assert user_version_of(path) == 2


def test_a_later_version_is_refused_and_left_as_it_is(tmp_path: Path) -> None:
    path = tmp_path / "state.db"
    SqliteState(path)
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA user_version = 3")
    connection.commit()
    connection.close()

    with pytest.raises(AecaError) as refusal:
        SqliteState(path)

    assert "3" in str(refusal.value)
    assert "back up" in refusal.value.next_action.lower()
    assert user_version_of(path) == 3


def test_a_state_in_memory_works_for_tests(tmp_path: Path) -> None:
    state = SqliteState(Path(":memory:"))

    assert state.attempts_increment("m1") == 1


# R16: an action that waits for approval is never run by a resume.


def test_actions_awaiting_approval_are_stored_in_the_journal_but_not_registered(
    state: SqliteState,
) -> None:
    entry = JournalEntry(
        source_message_id="m1",
        decision_json="{}",
        actions=(label_action(),),
        awaiting_approval=(reply_action(),),
    )

    state.journal_record(entry)

    assert state.journal_get("m1") == entry
    assert state.action_get("m1:label") is not None
    assert state.action_get("m1:reply") is None


def test_unfinished_actions_leave_out_every_approval_that_is_not_approved(
    state: SqliteState,
) -> None:
    state.approval_save(approval_of("a1", actions=(reply_action("k1"),)))
    state.approval_save(
        approval_of("a1", actions=(reply_action("k1"),), state=ApprovalState.REJECTED)
    )
    state.approval_save(
        approval_of(
            "a2",
            actions=(reply_action("k2"),),
            state=ApprovalState.SUPERSEDED,
            request_thread_id="r2",
        )
    )
    state.approval_save(approval_of("a3", actions=(reply_action("k3"),), request_thread_id="r3"))
    state.approval_save(
        approval_of(
            "a4", actions=(reply_action("k4"),), state=ApprovalState.FAILED, request_thread_id="r4"
        )
    )
    state.approval_save(
        approval_of(
            "a5",
            actions=(reply_action("k5"),),
            state=ApprovalState.EXECUTED,
            request_thread_id="r5",
        )
    )

    assert state.actions_unfinished() == []


def test_unfinished_actions_include_an_approved_approvals_pending_actions(
    state: SqliteState,
) -> None:
    state.approval_save(approval_of(state=ApprovalState.APPROVED))
    state.journal_record(entry_of(label_action("auto"), message_id="m2"))

    keys = [record.action.key for record in state.actions_unfinished()]

    assert keys == ["m1:reply", "auto"]


def test_an_approved_approvals_unfinished_action_stays_listed_after_a_restart(
    tmp_path: Path,
) -> None:
    path = tmp_path / "state.db"
    SqliteState(path).approval_save(approval_of(state=ApprovalState.APPROVED))

    keys = [record.action.key for record in SqliteState(path).actions_unfinished()]

    assert keys == ["m1:reply"]


# an approval's holds


def test_an_awaiting_approvals_holds_reach_the_holds_table(state: SqliteState) -> None:
    first, second = interval_starting_in(60), interval_starting_in(120)

    state.approval_save(approval_of(holds=(first, second)))

    assert state.holds_active(NOON) == [
        Hold(thread_id="t1", approval_id="appr-m1", interval=first),
        Hold(thread_id="t1", approval_id="appr-m1", interval=second),
    ]


def test_releasing_an_approval_removes_the_holds_it_saved(state: SqliteState) -> None:
    state.approval_save(approval_of(holds=(interval_starting_in(60), interval_starting_in(120))))

    state.holds_release("appr-m1")

    assert state.holds_active(NOON) == []


def test_saving_an_approval_again_replaces_its_holds(state: SqliteState) -> None:
    state.approval_save(approval_of(holds=(interval_starting_in(60), interval_starting_in(120))))

    state.approval_save(approval_of(holds=(interval_starting_in(180),)))

    assert [hold.interval for hold in state.holds_active(NOON)] == [interval_starting_in(180)]


# schema refusals


def table_names_of(path: Path) -> set[str]:
    connection = sqlite3.connect(path)
    try:
        rows = connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    finally:
        connection.close()
    return {row[0] for row in rows}


def write_lock_is_free(path: Path) -> bool:
    other = sqlite3.connect(path, timeout=0, isolation_level=None)
    try:
        other.execute("BEGIN IMMEDIATE")
        other.execute("ROLLBACK")
        return True
    except sqlite3.OperationalError:
        return False
    finally:
        other.close()


def test_a_file_with_a_foreign_table_at_version_zero_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "state.db"
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE notes (text TEXT)")
    connection.commit()
    connection.close()

    with pytest.raises(AecaError) as refusal:
        SqliteState(path)

    assert "back up" in refusal.value.next_action.lower()
    assert table_names_of(path) == {"notes"}
    assert write_lock_is_free(path)


def test_a_file_holding_a_journal_table_at_version_zero_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "state.db"
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE journal (source_message_id TEXT)")
    connection.commit()
    connection.close()

    with pytest.raises(AecaError):
        SqliteState(path)

    assert table_names_of(path) == {"journal"}


def test_a_version_three_file_is_refused_and_leaves_no_lock(tmp_path: Path) -> None:
    path = tmp_path / "state.db"
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA user_version = 3")
    connection.close()

    with pytest.raises(AecaError):
        SqliteState(path)

    assert write_lock_is_free(path)


def test_a_file_that_is_not_a_database_is_refused_with_a_next_action(tmp_path: Path) -> None:
    path = tmp_path / "state.db"
    path.write_bytes(b"this is not a sqlite file" * 100)

    with pytest.raises(AecaError) as refusal:
        SqliteState(path)

    assert str(path) in str(refusal.value)
    assert refusal.value.next_action


def test_a_read_only_file_is_refused_with_a_next_action(tmp_path: Path) -> None:
    path = tmp_path / "state.db"
    sqlite3.connect(path).close()
    path.chmod(0o400)

    with pytest.raises(AecaError) as refusal:
        SqliteState(path)

    assert refusal.value.next_action
    assert write_lock_is_free_or_unwritable(path)


def write_lock_is_free_or_unwritable(path: Path) -> bool:
    path.chmod(0o600)
    return write_lock_is_free(path)


# re-registering a key


def test_a_gated_action_journalled_as_automatic_is_refused(state: SqliteState) -> None:
    state.journal_record(entry_of(reply_action()))

    with pytest.raises(AecaError):
        state.approval_save(approval_of())

    assert state.approval_get("appr-m1") is None


def test_saving_the_same_approval_twice_is_a_no_op_for_its_actions(state: SqliteState) -> None:
    state.approval_save(approval_of())
    state.action_update("m1:reply", ActionStatus.EXECUTING, ref="draft-1")

    state.approval_save(approval_of())

    record = state.action_get("m1:reply")
    assert record is not None
    assert (record.status, record.ref) == (ActionStatus.EXECUTING, "draft-1")


def test_the_same_key_with_other_args_is_refused(state: SqliteState) -> None:
    state.approval_save(approval_of())
    changed = Action(verb=Verb.SEND_REPLY, key="m1:reply", args={"body": "Something else"})

    with pytest.raises(AecaError):
        state.approval_save(approval_of(actions=(changed,)))


def test_the_same_key_with_another_verb_is_refused(state: SqliteState) -> None:
    state.approval_save(approval_of())
    changed = Action(verb=Verb.STAR, key="m1:reply", args={"body": "Thanks"})

    with pytest.raises(AecaError):
        state.approval_save(approval_of(actions=(changed,)))


def test_the_same_key_under_another_approval_is_refused(state: SqliteState) -> None:
    state.approval_save(approval_of("a1"))

    with pytest.raises(AecaError):
        state.approval_save(approval_of("a2", request_thread_id="r2"))


# a failed commit


class CommitFailsOnce:
    """A connection whose first COMMIT fails, as a full disk or an I/O error would make it."""

    def __init__(self, real: sqlite3.Connection, *, rollback_fails: bool = False) -> None:
        self._real = real
        self._commit_pending_failure = True
        self._rollback_fails = rollback_fails

    def execute(self, sql: str, *parameters: object) -> sqlite3.Cursor:
        if sql == "COMMIT" and self._commit_pending_failure:
            self._commit_pending_failure = False
            raise sqlite3.OperationalError("disk I/O error")
        if sql == "ROLLBACK" and self._rollback_fails:
            raise sqlite3.OperationalError("rollback failed")
        return self._real.execute(sql, *parameters)  # type: ignore[arg-type]

    def __getattr__(self, name: str) -> object:
        return getattr(self._real, name)


def test_a_failed_commit_leaves_the_store_usable(state: SqliteState) -> None:
    state._connection = CommitFailsOnce(state._connection)  # type: ignore[assignment]

    with pytest.raises(sqlite3.OperationalError, match="disk I/O"):
        state.attempts_increment("m1")

    assert state.attempts_increment("m1") == 1


def test_a_failing_rollback_does_not_hide_the_original_error(state: SqliteState) -> None:
    state._connection = CommitFailsOnce(state._connection, rollback_fails=True)  # type: ignore[assignment]

    with pytest.raises(sqlite3.OperationalError, match="disk I/O"):
        state.attempts_increment("m1")


# approval order


def test_saving_an_approval_again_keeps_its_place_in_the_awaiting_order(
    state: SqliteState,
) -> None:
    state.approval_save(approval_of("a1", actions=(reply_action("k1"),)))
    state.approval_save(approval_of("a2", actions=(reply_action("k2"),), request_thread_id="r2"))

    state.approval_save(
        approval_of("a1", actions=(reply_action("k1"),), summary="Reply to Jürgen, revised")
    )

    assert [approval.id for approval in state.approvals_awaiting()] == ["a1", "a2"]


# F3: an entry stays incomplete until its email is audited and labelled processed.


def test_a_journal_entry_is_incomplete_until_it_is_completed(state: SqliteState) -> None:
    state.journal_record(entry_of(label_action(), message_id="m1"))
    state.journal_record(entry_of(label_action("m2:label"), message_id="m2"))

    before = [entry.source_message_id for entry in state.journal_incomplete()]
    state.journal_complete("m1")

    assert before == ["m1", "m2"]
    assert [entry.source_message_id for entry in state.journal_incomplete()] == ["m2"]


def test_an_entry_remembers_that_its_audit_line_was_written(state: SqliteState) -> None:
    state.journal_record(entry_of(label_action()))

    unaudited = state.journal_get("m1")
    state.journal_audited("m1")
    audited = state.journal_get("m1")

    assert unaudited is not None and unaudited.audited is False
    assert audited is not None and audited.audited is True
    assert [entry.audited for entry in state.journal_incomplete()] == [True]


def test_completing_or_auditing_a_message_with_no_entry_is_harmless(state: SqliteState) -> None:
    state.journal_complete("never-journalled")
    state.journal_audited("never-journalled")

    assert state.journal_incomplete() == []


# Version 1, exactly as Task 6 wrote it: the layout of the files already on the data volume.
VERSION_ONE_SCHEMA = """
CREATE TABLE journal (
    source_message_id TEXT PRIMARY KEY,
    decision_json TEXT NOT NULL,
    actions_json TEXT NOT NULL,
    awaiting_approval_json TEXT NOT NULL
);
CREATE TABLE actions (
    key TEXT PRIMARY KEY,
    sequence INTEGER NOT NULL,
    verb TEXT NOT NULL,
    args_json TEXT NOT NULL,
    source_message_id TEXT NOT NULL,
    approval_id TEXT,
    status TEXT NOT NULL,
    ref TEXT
);
CREATE TABLE approvals (
    id TEXT PRIMARY KEY,
    sequence INTEGER NOT NULL,
    source_message_id TEXT NOT NULL,
    source_thread_id TEXT NOT NULL,
    attempt INTEGER NOT NULL,
    actions_json TEXT NOT NULL,
    state TEXT NOT NULL,
    request_thread_id TEXT,
    summary TEXT NOT NULL,
    holds_json TEXT NOT NULL
);
CREATE INDEX approvals_by_request_thread ON approvals (request_thread_id);
CREATE TABLE holds (
    thread_id TEXT NOT NULL,
    approval_id TEXT NOT NULL,
    start TEXT NOT NULL,
    end TEXT NOT NULL
);
CREATE INDEX holds_by_approval ON holds (approval_id);
CREATE TABLE attempts (message_id TEXT PRIMARY KEY, count INTEGER NOT NULL);
CREATE TABLE answers_seen (message_id TEXT PRIMARY KEY);
CREATE TABLE incidents (kind TEXT PRIMARY KEY);
CREATE TABLE spend (day TEXT PRIMARY KEY, usd TEXT NOT NULL);
"""


def write_a_version_one_file(path: Path) -> None:
    connection = sqlite3.connect(path)
    connection.executescript(
        VERSION_ONE_SCHEMA + "INSERT INTO journal VALUES"
        " ('m1', '{\"label\":\"TASK\"}',"
        ' \'[{"verb": "apply_label", "key": "m1:label",'
        ' "args": {"label": "TASK"}}]\', \'[]\');'
        "INSERT INTO actions VALUES"
        " ('m1:label', 1, 'apply_label', '{\"label\": \"TASK\"}', 'm1', NULL, 'done', NULL);"
        "INSERT INTO attempts VALUES ('m2', 2);"
        "PRAGMA user_version = 1;"
    )
    connection.close()


def journal_columns_of(path: Path) -> set[str]:
    connection = sqlite3.connect(path)
    try:
        return {row[1] for row in connection.execute("PRAGMA table_info(journal)")}
    finally:
        connection.close()


def test_a_version_one_file_migrates_to_version_two_and_keeps_its_rows(tmp_path: Path) -> None:
    path = tmp_path / "state.db"
    write_a_version_one_file(path)

    state = SqliteState(path)

    assert user_version_of(path) == 2
    assert {"audited", "complete"} <= journal_columns_of(path)
    entry = state.journal_get("m1")
    assert entry is not None and entry.actions == (label_action(),)
    assert entry.audited is False
    assert [e.source_message_id for e in state.journal_incomplete()] == ["m1"]
    record = state.action_get("m1:label")
    assert record is not None and record.status is ActionStatus.DONE
    assert state.attempts_increment("m2") == 3


def test_a_migrated_file_opens_again_without_migrating_twice(tmp_path: Path) -> None:
    path = tmp_path / "state.db"
    write_a_version_one_file(path)
    SqliteState(path)

    reopened = SqliteState(path)

    assert user_version_of(path) == 2
    assert reopened.journal_get("m1") is not None


class VersionWriteFails:
    """A connection whose write of the new schema version fails, as a full disk would."""

    def __init__(self, real: sqlite3.Connection) -> None:
        self._real = real

    def execute(self, sql: str, *parameters: object) -> sqlite3.Cursor:
        if sql.startswith("PRAGMA user_version = 2"):
            raise sqlite3.OperationalError("disk I/O error")
        return self._real.execute(sql, *parameters)  # type: ignore[arg-type]

    def __getattr__(self, name: str) -> object:
        return getattr(self._real, name)

    def __setattr__(self, name: str, value: object) -> None:
        if name == "_real":
            object.__setattr__(self, name, value)
        else:
            setattr(self._real, name, value)


def test_a_failed_migration_leaves_the_file_at_version_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "state.db"
    write_a_version_one_file(path)
    real_connect = sqlite3.connect
    monkeypatch.setattr(
        "aeca.adapters.sqlite_state.sqlite3.connect",
        lambda *args, **kwargs: VersionWriteFails(real_connect(*args, **kwargs)),
    )

    with pytest.raises(AecaError) as refusal:
        SqliteState(path)
    monkeypatch.undo()

    assert "disk I/O error" in str(refusal.value)
    assert user_version_of(path) == 1
    assert journal_columns_of(path) == {
        "source_message_id", "decision_json", "actions_json", "awaiting_approval_json",
    }  # fmt: skip
    assert write_lock_is_free(path)
