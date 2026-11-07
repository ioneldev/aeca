"""The state store: SQLite behind the StateStore port. It is what makes actions run once."""

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from aeca.domain.actions import Action, ActionRecord, ActionStatus, Verb
from aeca.domain.approval import Approval, ApprovalState, Hold
from aeca.domain.errors import AecaError
from aeca.domain.journal import JournalEntry
from aeca.domain.time_range import Interval, in_utc

SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE journal (
    source_message_id TEXT PRIMARY KEY,
    decision_json TEXT NOT NULL,
    actions_json TEXT NOT NULL,
    awaiting_approval_json TEXT NOT NULL,
    audited INTEGER NOT NULL DEFAULT 0,
    complete INTEGER NOT NULL DEFAULT 0
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

_ACTION_COLUMNS = "key, verb, args_json, source_message_id, approval_id, status, ref"
# Added to the version-1 journal before its layout was frozen; a file written earlier lacks them.
_JOURNAL_FLAGS = ("audited", "complete")


class SqliteState:
    def __init__(self, path: Path) -> None:
        try:
            self._connection = sqlite3.connect(path, isolation_level=None)
        except sqlite3.Error as error:
            raise _unusable(path, error) from error
        self._connection.row_factory = sqlite3.Row
        try:
            self._prepare_schema(path)
        except AecaError:
            self._abandon_connection()
            raise
        except sqlite3.Error as error:
            self._abandon_connection()
            raise _unusable(path, error) from error

    def _prepare_schema(self, path: Path) -> None:
        found = self._connection.execute("PRAGMA user_version").fetchone()[0]
        if found == SCHEMA_VERSION:
            self._add_missing_journal_flags()
            return
        if found != 0:
            raise AecaError(
                f"{path} has schema version {found}, and this AECA reads version {SCHEMA_VERSION}",
                next_action=(
                    f"Back up {path}, then run the AECA release that wrote it to migrate it, "
                    "or move it aside to start with an empty state"
                ),
            )
        if self._holds_any_table():
            raise AecaError(
                f"{path} already holds tables but has no AECA schema version, so it is not "
                "an AECA state file",
                next_action=(
                    f"Back up {path}, then remove it or point AECA_DATA_DIR at another directory"
                ),
            )
        self._create_schema()

    def _add_missing_journal_flags(self) -> None:
        present = {row["name"] for row in self._connection.execute("PRAGMA table_info(journal)")}
        for flag in _JOURNAL_FLAGS:
            if flag not in present:
                self._connection.execute(
                    f"ALTER TABLE journal ADD COLUMN {flag} INTEGER NOT NULL DEFAULT 0"
                )

    def _holds_any_table(self) -> bool:
        row = self._connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        ).fetchone()
        return row is not None

    def _create_schema(self) -> None:
        # executescript commits first, so the whole creation is one explicit transaction here.
        self._connection.executescript(
            f"BEGIN IMMEDIATE;{_SCHEMA}PRAGMA user_version = {SCHEMA_VERSION};COMMIT;"
        )

    def _abandon_connection(self) -> None:
        self._roll_back_if_open()
        self._connection.close()

    def _roll_back_if_open(self) -> None:
        if not self._connection.in_transaction:
            return
        # The error that sent us here is the one worth reporting, not a second one from here.
        with suppress(sqlite3.Error):
            self._connection.execute("ROLLBACK")

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        self._connection.execute("BEGIN IMMEDIATE")
        try:
            yield self._connection
            self._connection.execute("COMMIT")
        except BaseException:
            self._roll_back_if_open()
            raise

    # journal and actions

    def journal_get(self, source_message_id: str) -> JournalEntry | None:
        row = self._connection.execute(
            "SELECT * FROM journal WHERE source_message_id = ?", (source_message_id,)
        ).fetchone()
        if row is None:
            return None
        return _journal_entry_from(row)

    # First write wins: the journal is the decision of record, and a second decision for the same
    # email must never replace the actions that may already have run.
    def journal_record(self, entry: JournalEntry) -> None:
        with self._transaction() as connection:
            inserted = connection.execute(
                "INSERT OR IGNORE INTO journal"
                " (source_message_id, decision_json, actions_json, awaiting_approval_json)"
                " VALUES (?, ?, ?, ?)",
                (
                    entry.source_message_id,
                    entry.decision_json,
                    _actions_to_json(entry.actions),
                    _actions_to_json(entry.awaiting_approval),
                ),
            )
            if inserted.rowcount == 1:
                self._register_actions(entry.actions, entry.source_message_id, approval_id=None)

    def journal_audited(self, source_message_id: str) -> None:
        with self._transaction() as connection:
            connection.execute(
                "UPDATE journal SET audited = 1 WHERE source_message_id = ?", (source_message_id,)
            )

    def journal_complete(self, source_message_id: str) -> None:
        with self._transaction() as connection:
            connection.execute(
                "UPDATE journal SET complete = 1 WHERE source_message_id = ?",
                (source_message_id,),
            )

    def journal_incomplete(self) -> list[JournalEntry]:
        rows = self._connection.execute(
            "SELECT * FROM journal WHERE complete = 0 ORDER BY rowid"
        ).fetchall()
        return [_journal_entry_from(row) for row in rows]

    def action_get(self, key: str) -> ActionRecord | None:
        row = self._connection.execute(
            f"SELECT {_ACTION_COLUMNS} FROM actions WHERE key = ?", (key,)
        ).fetchone()
        return None if row is None else _action_record_from(row)

    def action_update(self, key: str, status: ActionStatus, ref: str | None = None) -> None:
        with self._transaction() as connection:
            updated = connection.execute(
                "UPDATE actions SET status = ?, ref = COALESCE(?, ref) WHERE key = ?",
                (status.value, ref, key),
            )
            if updated.rowcount == 0:
                raise AecaError(
                    f"no action with key {key!r} is recorded",
                    next_action="Record the action in a journal entry or an approval first",
                )

    # An action that waits for approval is never listed: a resume would run it unapproved.
    def actions_unfinished(self) -> list[ActionRecord]:
        rows = self._connection.execute(
            "SELECT a.key, a.verb, a.args_json, a.source_message_id, a.approval_id, a.status, a.ref"
            " FROM actions a LEFT JOIN approvals p ON p.id = a.approval_id"
            " WHERE a.status IN (?, ?) AND (a.approval_id IS NULL OR p.state = ?)"
            " ORDER BY a.sequence",
            (
                ActionStatus.PENDING.value,
                ActionStatus.EXECUTING.value,
                ApprovalState.APPROVED.value,
            ),
        ).fetchall()
        return [_action_record_from(row) for row in rows]

    def _register_actions(
        self, actions: tuple[Action, ...], source_message_id: str, *, approval_id: str | None
    ) -> None:
        for action in actions:
            existing = self.action_get(action.key)
            if existing is None:
                self._insert_action(action, source_message_id, approval_id)
            else:
                _require_same_registration(existing, action, approval_id)

    def _insert_action(
        self, action: Action, source_message_id: str, approval_id: str | None
    ) -> None:
        self._connection.execute(
            "INSERT INTO actions (key, sequence, verb, args_json, source_message_id,"
            " approval_id, status, ref) VALUES (?, "
            "(SELECT COALESCE(MAX(sequence), 0) + 1 FROM actions), ?, ?, ?, ?, ?, NULL)",
            (
                action.key,
                action.verb.value,
                json.dumps(dict(action.args)),
                source_message_id,
                approval_id,
                ActionStatus.PENDING.value,
            ),
        )

    # approvals

    def approval_save(self, approval: Approval) -> None:
        with self._transaction() as connection:
            self._register_actions(
                approval.actions, approval.source_message_id, approval_id=approval.id
            )
            connection.execute(
                "INSERT INTO approvals (id, sequence, source_message_id, source_thread_id,"
                " attempt, actions_json, state, request_thread_id, summary, holds_json)"
                " VALUES (?, (SELECT COALESCE(MAX(sequence), 0) + 1 FROM approvals),"
                " ?, ?, ?, ?, ?, ?, ?, ?)"
                " ON CONFLICT(id) DO UPDATE SET"
                " source_message_id = excluded.source_message_id,"
                " source_thread_id = excluded.source_thread_id, attempt = excluded.attempt,"
                " actions_json = excluded.actions_json, state = excluded.state,"
                " request_thread_id = excluded.request_thread_id, summary = excluded.summary,"
                " holds_json = excluded.holds_json",
                (
                    approval.id,
                    approval.source_message_id,
                    approval.source_thread_id,
                    approval.attempt,
                    _actions_to_json(approval.actions),
                    approval.state.value,
                    approval.request_thread_id,
                    approval.summary,
                    json.dumps([_interval_to_json(interval) for interval in approval.holds]),
                ),
            )
            connection.execute("DELETE FROM holds WHERE approval_id = ?", (approval.id,))
            connection.executemany(
                "INSERT INTO holds VALUES (?, ?, ?, ?)",
                [
                    (
                        approval.source_thread_id,
                        approval.id,
                        interval.start.isoformat(),
                        interval.end.isoformat(),
                    )
                    for interval in approval.holds
                ],
            )

    def approval_get(self, approval_id: str) -> Approval | None:
        row = self._connection.execute(
            "SELECT * FROM approvals WHERE id = ?", (approval_id,)
        ).fetchone()
        return None if row is None else _approval_from(row)

    def approval_by_request_thread(self, thread_id: str) -> Approval | None:
        row = self._connection.execute(
            "SELECT * FROM approvals WHERE request_thread_id = ?", (thread_id,)
        ).fetchone()
        return None if row is None else _approval_from(row)

    def approvals_awaiting(self) -> list[Approval]:
        rows = self._connection.execute(
            "SELECT * FROM approvals WHERE state = ? ORDER BY sequence",
            (ApprovalState.AWAITING.value,),
        ).fetchall()
        return [_approval_from(row) for row in rows]

    # holds

    def holds_active(self, now: datetime) -> list[Hold]:
        rows = self._connection.execute("SELECT * FROM holds ORDER BY rowid").fetchall()
        holds = [_hold_from(row) for row in rows]
        return [hold for hold in holds if in_utc(hold.interval.start) > in_utc(now)]

    def holds_add(self, holds: list[Hold]) -> None:
        with self._transaction() as connection:
            connection.executemany(
                "INSERT INTO holds VALUES (?, ?, ?, ?)",
                [
                    (
                        hold.thread_id,
                        hold.approval_id,
                        hold.interval.start.isoformat(),
                        hold.interval.end.isoformat(),
                    )
                    for hold in holds
                ],
            )

    def holds_release(self, approval_id: str) -> None:
        with self._transaction() as connection:
            connection.execute("DELETE FROM holds WHERE approval_id = ?", (approval_id,))

    # attempts and answers

    def attempts_increment(self, message_id: str) -> int:
        with self._transaction() as connection:
            connection.execute(
                "INSERT INTO attempts VALUES (?, 1)"
                " ON CONFLICT(message_id) DO UPDATE SET count = count + 1",
                (message_id,),
            )
            row = connection.execute(
                "SELECT count FROM attempts WHERE message_id = ?", (message_id,)
            ).fetchone()
        return int(row["count"])

    def answer_seen(self, message_id: str) -> bool:
        row = self._connection.execute(
            "SELECT 1 FROM answers_seen WHERE message_id = ?", (message_id,)
        ).fetchone()
        return row is not None

    def answer_mark_seen(self, message_id: str) -> None:
        with self._transaction() as connection:
            connection.execute("INSERT OR IGNORE INTO answers_seen VALUES (?)", (message_id,))

    # incidents and spend

    def incident_open(self, kind: str) -> bool:
        with self._transaction() as connection:
            inserted = connection.execute("INSERT OR IGNORE INTO incidents VALUES (?)", (kind,))
        return inserted.rowcount == 1

    def incident_close(self, kind: str) -> None:
        with self._transaction() as connection:
            connection.execute("DELETE FROM incidents WHERE kind = ?", (kind,))

    def spend_add(self, day: date, usd: Decimal) -> Decimal:
        with self._transaction():
            total = self._spend_total(day) + usd
            self._connection.execute(
                "INSERT OR REPLACE INTO spend VALUES (?, ?)", (day.isoformat(), str(total))
            )
        return total

    def spend_on(self, day: date) -> Decimal:
        return self._spend_total(day)

    def _spend_total(self, day: date) -> Decimal:
        row = self._connection.execute(
            "SELECT usd FROM spend WHERE day = ?", (day.isoformat(),)
        ).fetchone()
        return Decimal(0) if row is None else Decimal(row["usd"])


def _unusable(path: Path, error: sqlite3.Error) -> AecaError:
    return AecaError(
        f"{path} cannot be used as the AECA state: {error}",
        next_action=(
            f"Back up {path}, then remove it or point AECA_DATA_DIR at another directory; "
            "if the file is fine, check that the directory is writable and the disk is not full"
        ),
    )


def _require_same_registration(
    existing: ActionRecord, action: Action, approval_id: str | None
) -> None:
    if existing.approval_id != approval_id:
        raise AecaError(
            f"action {action.key!r} is already registered"
            f" {_describe_gate(existing.approval_id)}, and is now being registered"
            f" {_describe_gate(approval_id)}",
            next_action=(
                "Fix the caller: an action that needs approval must only ever be registered"
                " through its approval, never in a journal entry's actions"
            ),
        )
    if existing.action != action:
        raise AecaError(
            f"action {action.key!r} is already registered with a different verb or arguments",
            next_action="Give the new action its own key, because a key names one action forever",
        )


def _describe_gate(approval_id: str | None) -> str:
    return "as automatic" if approval_id is None else f"under approval {approval_id!r}"


def _actions_to_json(actions: tuple[Action, ...]) -> str:
    return json.dumps([{"verb": a.verb.value, "key": a.key, "args": dict(a.args)} for a in actions])


def _actions_from_json(text: str) -> tuple[Action, ...]:
    return tuple(
        Action(verb=Verb(item["verb"]), key=item["key"], args=item["args"])
        for item in json.loads(text)
    )


def _interval_to_json(interval: Interval) -> list[str]:
    return [interval.start.isoformat(), interval.end.isoformat()]


def _interval_from_json(pair: list[str]) -> Interval:
    return Interval(start=datetime.fromisoformat(pair[0]), end=datetime.fromisoformat(pair[1]))


def _journal_entry_from(row: sqlite3.Row) -> JournalEntry:
    return JournalEntry(
        source_message_id=row["source_message_id"],
        decision_json=row["decision_json"],
        actions=_actions_from_json(row["actions_json"]),
        awaiting_approval=_actions_from_json(row["awaiting_approval_json"]),
        audited=bool(row["audited"]),
    )


def _action_record_from(row: sqlite3.Row) -> ActionRecord:
    return ActionRecord(
        action=Action(verb=Verb(row["verb"]), key=row["key"], args=json.loads(row["args_json"])),
        source_message_id=row["source_message_id"],
        approval_id=row["approval_id"],
        status=ActionStatus(row["status"]),
        ref=row["ref"],
    )


def _approval_from(row: sqlite3.Row) -> Approval:
    return Approval(
        id=row["id"],
        source_message_id=row["source_message_id"],
        source_thread_id=row["source_thread_id"],
        attempt=row["attempt"],
        actions=_actions_from_json(row["actions_json"]),
        state=ApprovalState(row["state"]),
        request_thread_id=row["request_thread_id"],
        summary=row["summary"],
        holds=tuple(_interval_from_json(pair) for pair in json.loads(row["holds_json"])),
    )


def _hold_from(row: sqlite3.Row) -> Hold:
    return Hold(
        thread_id=row["thread_id"],
        approval_id=row["approval_id"],
        interval=_interval_from_json([row["start"], row["end"]]),
    )
