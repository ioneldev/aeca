"""Runs recorded actions, each exactly once, even across a crash or a lost state file.

A label change is one modify of the source message, which is idempotent. A send goes through a
Gmail draft whose id is recorded before it is sent: after a crash, a recorded draft that still
exists is sent, and one that is gone was sent already, since Gmail deletes a draft it sends. A
reply also looks in its thread first, for a sent message carrying its key: that covers a lost
state file. An event's id derives from the source message, and it is read before it is inserted
(spec §4.4): a live event under it is the work done, and only a live one counts as done.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from aeca.application.failures import describe, ends_the_tick
from aeca.application.ports import Calendar, Mailbox, Tracer
from aeca.application.state_ports import StateStore
from aeca.domain.actions import Action, ActionRecord, ActionStatus, Verb
from aeca.domain.calendar_event import CalendarEvent, NewEvent
from aeca.domain.compose import ComposeRequest, compose_message
from aeca.domain.errors import EmailError
from aeca.domain.facts import EmailFacts
from aeca.domain.ids import event_id
from aeca.domain.labels import INBOX, PROCESSED, SENT, SPAM_SYSTEM_LABEL, STARRED
from aeca.domain.outgoing import OutgoingMessage
from aeca.domain.templates import EventDetails, event_description
from aeca.domain.time_range import Interval

SENDING_VERBS = frozenset({Verb.SEND_REPLY, Verb.NOTIFY_MANAGER, Verb.REQUEST_APPROVAL})
# A cancelled event under an id moves creation to the next attempt's id. A calendar that cancels
# every event it is given would move it forever, so the walk stops here and the action fails.
MAX_EVENT_ATTEMPTS = 5
# A message in a thread of its own has no thread to look in once the state file is lost, so the
# account's most recent sent mail is read instead, through the SENT label's index, which does not
# lag as search does. The known limit: a send older than this many sent messages is not found.
RECENT_SENT_SEARCHED = 25


@dataclass(frozen=True)
class ExecutorDeps:
    mailbox: Mailbox
    calendar: Calendar
    store: StateStore
    tracer: Tracer
    # MANAGER_ADDRESS, else the account itself: where the urgent notice goes.
    manager_address: str


class ActionFailed(EmailError):
    """One action failed for this email alone. It is marked failed, the actions after it stay
    pending, and records holds every action of the run as the store now has it."""

    def __init__(
        self, action: Action, *, records: tuple[ActionRecord, ...], cause: Exception
    ) -> None:
        super().__init__(
            f"{action.verb.value} failed: {describe(cause)}",
            next_action="AECA retries the email on the next polls; see trace.jsonl",
        )
        self.action = action
        self.records = records


@dataclass(frozen=True, slots=True)
class _Performed:
    ref: str | None
    sent_message_id: str | None = None


class _StepFailed(Exception):
    def __init__(self, action: Action, cause: Exception) -> None:
        super().__init__(str(cause))
        self.action = action
        self.cause = cause


class Executor:
    def __init__(self, deps: ExecutorDeps) -> None:
        self._deps = deps

    def run(self, actions: Sequence[Action], source: EmailFacts) -> tuple[ActionRecord, ...]:
        """Raises ActionFailed at the first action that fails for this email, and a system-wide
        error as it is, leaving that action as it was so a resume runs it."""
        recorded = [self._recorded(action) for action in actions]
        try:
            for record in recorded:
                self._run_once(record, source)
        except _StepFailed as failed:
            records = self._records_of(actions)
            raise ActionFailed(failed.action, records=records, cause=failed.cause) from failed.cause
        return self._records_of(actions)

    def _recorded(self, action: Action) -> ActionRecord:
        record = self._deps.store.action_get(action.key)
        if record is None:
            raise EmailError(
                f"action {action.key} ({action.verb.value}) was never recorded, so it is not run",
                next_action="Record every action in the journal or an approval before running it",
            )
        return record

    def _records_of(self, actions: Sequence[Action]) -> tuple[ActionRecord, ...]:
        return tuple(self._recorded(action) for action in actions)

    def _run_once(self, record: ActionRecord, source: EmailFacts) -> None:
        if record.status is ActionStatus.DONE:
            return
        action = record.action
        try:
            performed = self._perform(record, source)
        except Exception as error:
            if ends_the_tick(error):
                raise
            self._mark_failed(action, error)
            raise _StepFailed(action, error) from error
        self._deps.store.action_update(action.key, ActionStatus.DONE, ref=performed.ref)
        if performed.sent_message_id is not None:
            # So the inbox query never returns it, when it comes back with INBOX.
            self._deps.mailbox.modify(performed.sent_message_id, add=[PROCESSED], remove=[])

    def _perform(self, record: ActionRecord, source: EmailFacts) -> _Performed:
        if record.action.verb in SENDING_VERBS:
            return self._send_once(record, source)
        if record.action.verb is Verb.CREATE_EVENT:
            return self._create_event_once(record, source)
        add, remove = _label_change(record.action)
        self._deps.mailbox.modify(source.message_id, add=add, remove=remove)
        return _Performed(ref=None)

    def _send_once(self, record: ActionRecord, source: EmailFacts) -> _Performed:
        if record.ref is not None:
            return self._finish_recorded_draft(record.ref)
        message = self._message(record, source)
        already_sent = self._already_sent(message)
        if already_sent is not None:
            return _Performed(ref=already_sent)
        draft_id = self._deps.mailbox.create_draft(message)
        self._deps.store.action_update(record.action.key, ActionStatus.EXECUTING, ref=draft_id)
        return self._send_draft(draft_id)

    # The store has no record of a draft, so this is a first send, or the state file was lost.
    def _already_sent(self, message: OutgoingMessage) -> str | None:
        if message.thread_id is None:
            return self._deps.mailbox.recently_sent_with_aeca_id(
                message.aeca_id, RECENT_SENT_SEARCHED
            )
        return self._sent_in_thread(message.thread_id, message.aeca_id)

    def _finish_recorded_draft(self, draft_id: str) -> _Performed:
        if self._deps.mailbox.draft_exists(draft_id):
            return self._send_draft(draft_id)
        return _Performed(ref=draft_id)

    def _send_draft(self, draft_id: str) -> _Performed:
        sent = self._deps.mailbox.send_draft(draft_id)
        return _Performed(ref=sent.message_id, sent_message_id=sent.message_id)

    # SENT is a label only the account can give a message, so a sender cannot forge this match.
    def _sent_in_thread(self, thread_id: str, aeca_id: str) -> str | None:
        return next(
            (
                summary.message_id
                for summary in self._deps.mailbox.thread_summaries(thread_id)
                if SENT in summary.label_ids and summary.aeca_id == aeca_id
            ),
            None,
        )

    def _create_event_once(self, record: ActionRecord, source: EmailFacts) -> _Performed:
        calendar = self._deps.calendar
        for attempt in range(MAX_EVENT_ATTEMPTS):
            candidate = event_id(source.message_id, attempt)
            found = calendar.get_event(candidate)
            if found is None:
                calendar.insert_event(self._new_event(record, source, new_id=candidate))
                found = calendar.get_event(candidate)
            if _is_live(found):
                return _Performed(ref=candidate)
            if found is None:
                raise EmailError(
                    f"the calendar answered the insert of event {candidate}, then did not have it",
                    next_action="Check the calendar; AECA retries, and confirms nothing meanwhile",
                )
        raise EmailError(
            f"every event id for {source.message_id} up to attempt {MAX_EVENT_ATTEMPTS} is "
            "cancelled",
            next_action="Check the calendar: something deletes the events AECA creates",
        )

    def _new_event(self, record: ActionRecord, source: EmailFacts, *, new_id: str) -> NewEvent:
        args = record.action.args
        slot = Interval(datetime.fromisoformat(args["start"]), datetime.fromisoformat(args["end"]))
        details = EventDetails(
            facts=source,
            slot=slot,
            zone=ZoneInfo(args["zone"]),
            summary=self._summary_of_approval(record.approval_id),
            approval_id=record.approval_id or "",
            # An event action recorded before the quoted times were kept has none.
            quoted_times=args.get("quoted", ""),
        )
        return NewEvent(
            id=new_id, title=args["title"], description=event_description(details), interval=slot
        )

    def _summary_of_approval(self, approval_id: str | None) -> str:
        approval = None if approval_id is None else self._deps.store.approval_get(approval_id)
        return "" if approval is None else approval.summary

    def _message(self, record: ActionRecord, source: EmailFacts) -> OutgoingMessage:
        return compose_message(
            ComposeRequest(
                action=record.action,
                facts=source,
                manager_address=self._deps.manager_address,
                approved=record.approval_id is not None,
            )
        )

    def _mark_failed(self, action: Action, error: Exception) -> None:
        self._deps.store.action_update(action.key, ActionStatus.FAILED)
        self._deps.tracer.event(
            "action_failed", key=action.key, verb=action.verb.value, error=describe(error)
        )


def _is_live(event: CalendarEvent | None) -> bool:
    return event is not None and not event.cancelled


def _label_change(action: Action) -> tuple[list[str], list[str]]:
    """What the verb adds to and removes from the source message."""
    if action.verb is Verb.APPLY_LABEL:
        return [action.args["label"]], []
    if action.verb is Verb.STAR:
        return [STARRED], []
    if action.verb is Verb.ARCHIVE:
        return [], [INBOX]
    if action.verb is Verb.MOVE_TO_SPAM:
        # The system label moves the message to Spam; Gmail refuses a user label of that name.
        return [SPAM_SYSTEM_LABEL], []
    raise EmailError(
        f"the executor does not run {action.verb.value}",
        next_action="Run it through the approval that owns it",
    )
