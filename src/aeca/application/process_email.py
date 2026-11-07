"""One inbox email, from a fresh read to its actions, its approval, its audit line and
AECA/processed.

The decision and its plan are journalled before the first side effect (spec §4.6). A crash at any
later point resumes from the journal, by message id and without asking the model again, and the
executor runs each action once. The journal entry stays incomplete until the email is audited and
labelled processed, so even an email that already left the Inbox is finished.
"""

from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime

from aeca.application.budget import Budget, Spend
from aeca.application.executor import Executor
from aeca.application.failures import describe, ends_the_tick
from aeca.application.meetings import MeetingRequest, plan_meeting
from aeca.application.ports import (
    FIRST_ATTEMPT,
    ApprovalOpener,
    Calendar,
    Clock,
    Mailbox,
    MessageSummary,
    OpenApproval,
    Tracer,
)
from aeca.application.state_ports import AuditLog, StateStore
from aeca.domain.actions import Action, ActionStatus
from aeca.domain.approval import ApprovalState
from aeca.domain.audit import ActionSummary, AuditLine, Outcome, action_summary
from aeca.domain.decision import Decision
from aeca.domain.errors import EmailError
from aeca.domain.facts import EmailFacts, FactSettings, extract_facts
from aeca.domain.ids import approval_id
from aeca.domain.journal import JournalEntry, JournalledDecision
from aeca.domain.labels import AWAITING, ERROR, PROCESSED, SENT
from aeca.domain.meeting_outcome import AlreadyOnCalendar, MeetingOutcome
from aeca.domain.planner import PlanSettings
from aeca.domain.policy import INBOX_REMOVERS, PolicyContext, PolicySettings, plan_actions
from aeca.domain.replies import ReplyContext, reply_block_reason
from aeca.domain.templates import APPROVAL_REQUEST_KIND, AUTOMATIC_REPLY_KINDS
from aeca.domain.text import bounded

MAX_TRIES = 3
ERROR_TEXT_LIMIT = 300
INVITATION_ON_CALENDAR = "invitation_on_calendar"
UNFINISHED_STATUSES = frozenset({ActionStatus.PENDING, ActionStatus.EXECUTING})


@dataclass(frozen=True)
class InboxSettings:
    facts: FactSettings
    plan: PlanSettings
    policy: PolicySettings


@dataclass(frozen=True)
class InboxDeps:
    mailbox: Mailbox
    calendar: Calendar
    store: StateStore
    audit: AuditLog
    tracer: Tracer
    clock: Clock
    # The decision pays for its own model calls, and the budget is where that shows.
    budget: Budget
    decide: Callable[[EmailFacts], Decision]
    executor: Executor
    approvals: ApprovalOpener
    settings: InboxSettings


@dataclass(frozen=True, slots=True)
class _Incoming:
    """An email to process, with its thread read once, before the model is asked anything."""

    facts: EmailFacts
    thread: tuple[MessageSummary, ...]


@dataclass(frozen=True, slots=True)
class _OpenedApproval:
    id: str
    state: ApprovalState


@dataclass(frozen=True, slots=True)
class _Progress:
    """What the journal says about an email: its decision, and each action as far as it got."""

    journalled: JournalledDecision
    actions: tuple[ActionSummary, ...]


@dataclass(frozen=True, slots=True)
class _Finished:
    facts: EmailFacts
    progress: _Progress
    approval: _OpenedApproval | None
    spend: Spend


@dataclass(frozen=True, slots=True)
class _Failure:
    message_id: str
    facts: EmailFacts | None
    error: Exception
    spend: Spend
    progress: _Progress | None = None
    approval: _OpenedApproval | None = None


class InboxProcessor:
    def __init__(self, deps: InboxDeps) -> None:
        self._deps = deps

    def unfinished(self) -> list[str]:
        """Emails journalled but not yet audited and labelled processed, oldest first.

        They resume by message id, because an archived email no longer matches the inbox query."""
        return [entry.source_message_id for entry in self._deps.store.journal_incomplete()]

    def process(self, message_id: str) -> AuditLine | None:
        """None means the email is not AECA's to classify: it was skipped.

        A system-wide error is raised to the caller, and the email is charged nothing for it. An
        EmailError is raised when even the failure could not be recorded."""
        spend_before = self._deps.budget.spent
        known_facts: EmailFacts | None = None
        try:
            raw = self._deps.mailbox.get(message_id)
            if PROCESSED in raw.label_ids:
                # A kill between the label and the journal's completion lands here on resume.
                self._deps.store.journal_complete(message_id)
                return None
            known_facts = extract_facts(raw, self._deps.settings.facts)
            return self._process_unprocessed(known_facts, spend_before)
        except Exception as error:
            if ends_the_tick(error):
                raise
            spend = self._deps.budget.spent - spend_before
            failure = _Failure(message_id=message_id, facts=known_facts, error=error, spend=spend)
            return self._record_failure(failure)

    def _process_unprocessed(self, facts: EmailFacts, spend_before: Spend) -> AuditLine | None:
        if facts.aeca_kind is not None and facts.sent_by_account:
            self._mark_processed(facts)
            return None
        thread = tuple(self._deps.mailbox.thread_summaries(facts.thread_id))
        incoming = _Incoming(facts=facts, thread=thread)
        if self._is_approval_thread(incoming):
            self._label_if_its_approval_closed(facts)
            return None
        return self._finish(incoming, spend_before)

    # By the thread's first message, which holds without any state (spec §4.5), and by the store.
    # The manager's answer is left unlabelled for the approval loop, which reads it.
    def _is_approval_thread(self, incoming: _Incoming) -> bool:
        if _starts_with_an_approval_request(incoming.thread):
            return True
        return self._deps.store.approval_by_request_thread(incoming.facts.thread_id) is not None

    # The approval loop reads only the threads of open approvals, so an answer that arrives after
    # its approval closed is read by nobody: labelled here, it stops coming back every tick.
    def _label_if_its_approval_closed(self, facts: EmailFacts) -> None:
        approval = self._deps.store.approval_by_request_thread(facts.thread_id)
        if approval is None or approval.is_open:
            return
        self._mark_processed(facts)
        self._deps.tracer.event(
            "answer_after_close", message_id=facts.message_id, approval_id=approval.id
        )

    def _finish(self, incoming: _Incoming, spend_before: Spend) -> AuditLine:
        facts = incoming.facts
        entry = self._journal_entry(incoming)
        approval = self._carry_out(facts, entry)
        finished = _Finished(
            facts=facts,
            progress=self._progress_of(entry),
            approval=approval,
            spend=self._deps.budget.spent - spend_before,
        )
        line = _done_line(self._deps.clock.now(), finished)
        if not entry.audited:
            self._deps.audit.write(line)
            self._deps.store.journal_audited(facts.message_id)
        self._mark_processed(facts)
        self._deps.store.journal_complete(facts.message_id)
        return line

    # The journal's first write wins, so a resume works from the decision already recorded.
    def _journal_entry(self, incoming: _Incoming) -> JournalEntry:
        message_id = incoming.facts.message_id
        recorded = self._deps.store.journal_get(message_id)
        if recorded is not None:
            self._deps.tracer.event("email_resumed", message_id=message_id)
            return recorded
        entry = self._planned_entry(incoming)
        self._deps.store.journal_record(entry)
        return entry

    def _planned_entry(self, incoming: _Incoming) -> JournalEntry:
        facts = incoming.facts
        decision = self._deps.decide(facts)
        meeting = self._plan_meeting(facts, decision)
        plan = plan_actions(
            PolicyContext(
                decision=decision,
                facts=facts,
                meeting=meeting,
                settings=self._deps.settings.policy,
                reply_block=self._reply_block(incoming, decision),
            )
        )
        notes = (INVITATION_ON_CALENDAR,) if isinstance(meeting, AlreadyOnCalendar) else ()
        journalled = JournalledDecision.of(decision, holds=plan.holds, notes=notes)
        return JournalEntry(
            source_message_id=facts.message_id,
            decision_json=journalled.to_json(),
            actions=plan.automatic,
            awaiting_approval=plan.for_approval,
        )

    def _plan_meeting(self, facts: EmailFacts, decision: Decision) -> MeetingOutcome | None:
        now = self._deps.clock.now()
        return plan_meeting(
            MeetingRequest(
                facts=facts,
                decision=decision,
                calendar=self._deps.calendar,
                holds=self._deps.store.holds_active(now),
                settings=self._deps.settings.plan,
                now=now,
            )
        )

    def _reply_block(self, incoming: _Incoming, decision: Decision) -> str | None:
        context = ReplyContext(
            label=decision.label,
            account_address=self._deps.settings.facts.account_address,
            thread_has_unapproved_reply=_has_an_unapproved_reply(incoming.thread),
        )
        return reply_block_reason(incoming.facts, context)

    # The approval is opened before the email leaves the Inbox: the verbs that remove it run last
    # (spec §4.2), and a crash after an archive leaves those still pending for a resume to find,
    # where an approval left unopened would be found by nothing.
    def _carry_out(self, facts: EmailFacts, entry: JournalEntry) -> _OpenedApproval | None:
        stays_in_inbox = tuple(a for a in entry.actions if a.verb not in INBOX_REMOVERS)
        leaves_inbox = tuple(a for a in entry.actions if a.verb in INBOX_REMOVERS)
        self._deps.executor.run(stays_in_inbox, facts)
        approval = self._open_approval(facts, entry) if entry.awaiting_approval else None
        self._deps.executor.run(leaves_inbox, facts)
        return approval

    def _open_approval(self, facts: EmailFacts, entry: JournalEntry) -> _OpenedApproval:
        journalled = JournalledDecision.from_json(entry.decision_json)
        opened_id = self._deps.approvals.open(
            OpenApproval(
                source=facts,
                actions=entry.awaiting_approval,
                holds=journalled.holds,
                summary=journalled.summary,
            )
        )
        stored = self._deps.store.approval_get(opened_id)
        if stored is None:
            raise EmailError(
                f"the approval opener returned {opened_id}, which the state store does not hold",
                next_action="Fix the approval opener: it must save the approval it opens",
            )
        if stored.state is ApprovalState.AWAITING:
            self._deps.mailbox.modify(facts.message_id, add=[AWAITING], remove=[])
        return _OpenedApproval(id=opened_id, state=stored.state)

    def _progress_of(self, entry: JournalEntry) -> _Progress:
        planned = (*entry.actions, *entry.awaiting_approval)
        return _Progress(
            journalled=JournalledDecision.from_json(entry.decision_json),
            actions=tuple(self._summary_of(action) for action in planned),
        )

    def _summary_of(self, action: Action) -> ActionSummary:
        record = self._deps.store.action_get(action.key)
        return action_summary(action, ActionStatus.PENDING if record is None else record.status)

    # An email that failed before carries AECA/error; once it is processed the label is a lie.
    def _mark_processed(self, facts: EmailFacts) -> None:
        stale = [ERROR] if ERROR in facts.label_ids else []
        self._deps.mailbox.modify(facts.message_id, add=[PROCESSED], remove=stale)

    def _record_failure(self, failure: _Failure) -> AuditLine:
        try:
            attempts = self._deps.store.attempts_increment(failure.message_id)
            if attempts >= MAX_TRIES:
                self._give_up(failure.message_id)
            line = _failed_line(self._deps.clock.now(), self._with_progress(failure))
            self._deps.audit.write(line)
            labels = [ERROR, PROCESSED] if attempts >= MAX_TRIES else [ERROR]
            self._deps.mailbox.modify(failure.message_id, add=labels, remove=[])
            self._deps.tracer.event(
                "email_failed",
                message_id=failure.message_id,
                attempts=attempts,
                error=repr(failure.error),
            )
        except Exception as recording_error:
            if ends_the_tick(recording_error):
                raise
            raise EmailError(
                f"Could not record the failure of {failure.message_id}: "
                f"{describe(recording_error)}",
                next_action="Check the data volume and the Gmail account, then wait for the retry",
            ) from recording_error
        return line

    def _with_progress(self, failure: _Failure) -> _Failure:
        entry = self._deps.store.journal_get(failure.message_id)
        if entry is None:
            return failure
        return replace(
            failure,
            progress=self._progress_of(entry),
            approval=self._approval_of(failure.message_id),
        )

    # Approval ids derive from the source message and the attempt (spec §4.5), and an email from
    # the inbox is on its first attempt, so the approval is found without asking the opener.
    def _approval_of(self, message_id: str) -> _OpenedApproval | None:
        stored = self._deps.store.approval_get(approval_id(message_id, FIRST_ATTEMPT))
        return None if stored is None else _OpenedApproval(id=stored.id, state=stored.state)

    # Done before the labels, so that an email Gmail can no longer label still stops being resumed.
    def _give_up(self, message_id: str) -> None:
        entry = self._deps.store.journal_get(message_id)
        for action in entry.actions if entry is not None else ():
            record = self._deps.store.action_get(action.key)
            if record is not None and record.status in UNFINISHED_STATUSES:
                self._deps.store.action_update(action.key, ActionStatus.FAILED)
        self._deps.store.journal_complete(message_id)


def _starts_with_an_approval_request(thread: tuple[MessageSummary, ...]) -> bool:
    if not thread:
        return False
    first = thread[0]
    return SENT in first.label_ids and first.aeca_kind == APPROVAL_REQUEST_KIND


# An approved reply carries a kind of its own, so only an automatic one uses up the thread's one
# unapproved reply (spec §4.3).
def _has_an_unapproved_reply(thread: tuple[MessageSummary, ...]) -> bool:
    return any(
        SENT in summary.label_ids and summary.aeca_kind in AUTOMATIC_REPLY_KINDS
        for summary in thread
    )


def _done_line(now: datetime, finished: _Finished) -> AuditLine:
    facts, journalled, approval = finished.facts, finished.progress.journalled, finished.approval
    waits = approval is not None and approval.state is ApprovalState.AWAITING
    return AuditLine(
        time=now,
        message_id=facts.message_id,
        thread_id=facts.thread_id,
        sender=facts.sender,
        subject=facts.subject,
        label=journalled.label,
        urgent=journalled.urgent,
        decided_by=journalled.decided_by,
        rules=journalled.rules_fired + journalled.notes,
        actions=finished.progress.actions,
        approval_id=None if approval is None else approval.id,
        approval_state=None if approval is None else approval.state.value,
        outcome=Outcome.AWAITING_APPROVAL if waits else Outcome.DONE,
        error=None,
        tokens_in=finished.spend.tokens_in,
        tokens_out=finished.spend.tokens_out,
        usd=finished.spend.usd,
    )


def _failed_line(now: datetime, failure: _Failure) -> AuditLine:
    facts, progress, approval = failure.facts, failure.progress, failure.approval
    journalled = progress.journalled if progress is not None else None
    return AuditLine(
        time=now,
        message_id=failure.message_id,
        thread_id=facts.thread_id if facts else "",
        sender=facts.sender if facts else "",
        subject=facts.subject if facts else "",
        label=journalled.label if journalled else "",
        urgent=journalled.urgent if journalled else False,
        decided_by=journalled.decided_by if journalled else "",
        rules=journalled.rules_fired + journalled.notes if journalled else (),
        actions=progress.actions if progress is not None else (),
        approval_id=None if approval is None else approval.id,
        approval_state=None if approval is None else approval.state.value,
        outcome=Outcome.FAILED,
        error=bounded(describe(failure.error), ERROR_TEXT_LIMIT),
        tokens_in=failure.spend.tokens_in,
        tokens_out=failure.spend.tokens_out,
        usd=failure.spend.usd,
    )
