"""The state store: SQLite behind the StateStore port. It is what makes actions run once."""

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from aeca.domain.actions import Action, ActionRecord, ActionStatus, Verb
from aeca.domain.approval import Approval, ApprovalState, Hold
from aeca.domain.errors import AecaError
from aeca.domain.journal import JournalEntry
from aeca.domain.time_range import Interval

SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE journal (
    source_message_id TEXT PRIMARY KEY,
    decision_json TEXT NOT NULL,
    actions_json TEXT NOT NULL
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


class SqliteState:
    def __init__(self, path: Path) -> None:
        self._connection = sqlite3.connect(path, isolation_level=None)
        self._connection.row_factory = sqlite3.Row
        self._prepare_schema(path)

    def _prepare_schema(self, path: Path) -> None:
        found = self._connection.execute("PRAGMA user_version").fetchone()[0]
        if found == SCHEMA_VERSION:
            return
        if found != 0:
            self._connection.close()
            raise AecaError(
                f"{path} has schema version {found}, and this AECA reads version {SCHEMA_VERSION}",
                next_action=(
                    f"Back up {path}, then run the AECA release that wrote it to migrate it, "
                    "or move it aside to start with an empty state"
                ),
            )
        self._create_schema()

    def _create_schema(self) -> None:
        # executescript commits first, so the whole creation is one explicit transaction here.
        self._connection.executescript(
            f"BEGIN IMMEDIATE;{_SCHEMA}PRAGMA user_version = {SCHEMA_VERSION};COMMIT;"
        )

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        self._connection.execute("BEGIN IMMEDIATE")
        try:
            yield self._connection
        except BaseException:
            self._connection.execute("ROLLBACK")
            raise
        self._connection.execute("COMMIT")

    # journal and actions

    def journal_get(self, source_message_id: str) -> JournalEntry | None:
        row = self._connection.execute(
            "SELECT * FROM journal WHERE source_message_id = ?", (source_message_id,)
        ).fetchone()
        if row is None:
            return None
        return JournalEntry(
            source_message_id=row["source_message_id"],
            decision_json=row["decision_json"],
            actions=_actions_from_json(row["actions_json"]),
        )

    # First write wins: the journal is the decision of record, and a second decision for the same
    # email must never replace the actions that may already have run.
    def journal_record(self, entry: JournalEntry) -> None:
        with self._transaction() as connection:
            inserted = connection.execute(
                "INSERT OR IGNORE INTO journal VALUES (?, ?, ?)",
                (entry.source_message_id, entry.decision_json, _actions_to_json(entry.actions)),
            )
            if inserted.rowcount == 1:
                self._register_actions(entry.actions, entry.source_message_id, approval_id=None)

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

    def actions_unfinished(self) -> list[ActionRecord]:
        rows = self._connection.execute(
            f"SELECT {_ACTION_COLUMNS} FROM actions WHERE status IN (?, ?) ORDER BY sequence",
            (ActionStatus.PENDING.value, ActionStatus.EXECUTING.value),
        ).fetchall()
        return [_action_record_from(row) for row in rows]

    def _register_actions(
        self, actions: tuple[Action, ...], source_message_id: str, *, approval_id: str | None
    ) -> None:
        for action in actions:
            # Ignored when the key exists, so saving an approval again never resets progress.
            self._connection.execute(
                "INSERT OR IGNORE INTO actions (key, sequence, verb, args_json, source_message_id,"
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
            connection.execute(
                "INSERT OR REPLACE INTO approvals VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
            self._register_actions(
                approval.actions, approval.source_message_id, approval_id=approval.id
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
            "SELECT * FROM approvals WHERE state = ? ORDER BY rowid",
            (ApprovalState.AWAITING.value,),
        ).fetchall()
        return [_approval_from(row) for row in rows]

    # holds

    def holds_active(self, now: datetime) -> list[Hold]:
        rows = self._connection.execute("SELECT * FROM holds ORDER BY rowid").fetchall()
        holds = [_hold_from(row) for row in rows]
        return [hold for hold in holds if hold.interval.start > now]

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
