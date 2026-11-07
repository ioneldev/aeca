"""One inbox email, from a fresh read to its actions, its approval, its audit line and
AECA/processed.

The decision and its plan are journalled before the first side effect (spec §4.6). A crash at any
later point resumes from the journal, by message id and without asking the model again, and the
executor runs each action once.
"""

from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime

from aeca.application.budget import Budget, Spend
from aeca.application.executor import Executor
from aeca.application.failures import describe, ends_the_tick
from aeca.application.meetings import MeetingRequest, plan_meeting
from aeca.application.ports import ApprovalOpener, Calendar, Clock, Mailbox, OpenApproval, Tracer
from aeca.application.state_ports import AuditLog, StateStore
from aeca.domain.actions import Action, ActionStatus
from aeca.domain.approval import ApprovalState
from aeca.domain.audit import ActionSummary, AuditLine, Outcome
from aeca.domain.decision import Decision
from aeca.domain.errors import EmailError
from aeca.domain.facts import EmailFacts, FactSettings, extract_facts
from aeca.domain.journal import JournalEntry, JournalledDecision
from aeca.domain.labels import AWAITING, ERROR, PROCESSED, ReplyKind
from aeca.domain.meeting_outcome import AlreadyOnCalendar, MeetingOutcome
from aeca.domain.planner import PlanSettings
from aeca.domain.policy import INBOX_REMOVERS, PolicyContext, PolicySettings, plan_actions
from aeca.domain.replies import ReplyContext, reply_block_reason
from aeca.domain.text import bounded

MAX_TRIES = 3
ERROR_TEXT_LIMIT = 300
SENT = "SENT"
INVITATION_ON_CALENDAR = "invitation_on_calendar"
# The reply kinds AECA sends without approval: one of them in a thread uses up the thread's
# unapproved reply (spec §4.3). An approved reply, such as a confirmation, does not.
UNAPPROVED_REPLY_KINDS = frozenset(kind.value for kind in ReplyKind if kind is not ReplyKind.NONE)
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


class InboxProcessor:
    def __init__(self, deps: InboxDeps) -> None:
        self._deps = deps

    def unfinished(self) -> list[str]:
        """Emails whose automatic actions a crash or an outage left unfinished, oldest first.

        They resume by message id, because an archived email no longer matches the inbox query.
        An approval's actions are the approval loop's to run, so they are left out."""
        unfinished = self._deps.store.actions_unfinished()
        owners = (record.source_message_id for record in unfinished if record.approval_id is None)
        return list(dict.fromkeys(owners))

    def process(self, message_id: str) -> AuditLine | None:
        """None means the email is not AECA's to classify: it was skipped.

        A system-wide error is raised to the caller, and the email is charged nothing for it. An
        EmailError is raised when even the failure could not be recorded."""
        spend_before = self._deps.budget.spent
        known_facts: EmailFacts | None = None
        try:
            raw = self._deps.mailbox.get(message_id)
            if PROCESSED in raw.label_ids:
                return None
            known_facts = extract_facts(raw, self._deps.settings.facts)
            if self._is_skipped(known_facts):
                return None
            return self._finish(known_facts, spend_before)
        except Exception as error:
            if ends_the_tick(error):
                raise
            spend = self._deps.budget.spent - spend_before
            return self._record_failure(_Failure(message_id, known_facts, error, spend))

    def _is_skipped(self, facts: EmailFacts) -> bool:
        if facts.aeca_kind is not None and facts.sent_by_account:
            self._mark_processed(facts)
            return True
        # The manager's answer is left unlabelled for the approval loop, which reads it.
        return self._deps.store.approval_by_request_thread(facts.thread_id) is not None

    def _finish(self, facts: EmailFacts, spend_before: Spend) -> AuditLine:
        entry = self._journal_entry(facts)
        approval = self._carry_out(facts, entry)
        finished = _Finished(
            facts=facts,
            progress=self._progress_of(entry),
            approval=approval,
            spend=self._deps.budget.spent - spend_before,
        )
        line = _done_line(self._deps.clock.now(), finished)
        self._deps.audit.write(line)
        self._mark_processed(facts)
        return line

    # The journal's first write wins, so a resume works from the decision already recorded.
    def _journal_entry(self, facts: EmailFacts) -> JournalEntry:
        recorded = self._deps.store.journal_get(facts.message_id)
        if recorded is not None:
            self._deps.tracer.event("email_resumed", message_id=facts.message_id)
            return recorded
        entry = self._planned_entry(facts)
        self._deps.store.journal_record(entry)
        return entry

    def _planned_entry(self, facts: EmailFacts) -> JournalEntry:
        decision = self._deps.decide(facts)
        meeting = self._plan_meeting(facts, decision)
        plan = plan_actions(
            PolicyContext(
                decision=decision,
                facts=facts,
                meeting=meeting,
                settings=self._deps.settings.policy,
                reply_block=self._reply_block(facts, decision),
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

    def _reply_block(self, facts: EmailFacts, decision: Decision) -> str | None:
        context = ReplyContext(
            label=decision.label,
            account_address=self._deps.settings.facts.account_address,
            thread_has_unapproved_reply=self._thread_has_unapproved_reply(facts.thread_id),
        )
        return reply_block_reason(facts, context)

    def _thread_has_unapproved_reply(self, thread_id: str) -> bool:
        return any(
            SENT in summary.label_ids and summary.aeca_kind in UNAPPROVED_REPLY_KINDS
            for summary in self._deps.mailbox.thread_summaries(thread_id)
        )

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
        state = ApprovalState.AWAITING if stored is None else stored.state
        if state is ApprovalState.AWAITING:
            self._deps.mailbox.modify(facts.message_id, add=[AWAITING], remove=[])
        return _OpenedApproval(id=opened_id, state=state)

    def _progress_of(self, entry: JournalEntry) -> _Progress:
        planned = (*entry.actions, *entry.awaiting_approval)
        return _Progress(
            journalled=JournalledDecision.from_json(entry.decision_json),
            actions=tuple(self._summary_of(action) for action in planned),
        )

    def _summary_of(self, action: Action) -> ActionSummary:
        record = self._deps.store.action_get(action.key)
        status = ActionStatus.PENDING if record is None else record.status
        detail = action.args.get("label") or action.args.get("template") or ""
        return (action.verb.value, detail, status.value)

    # An email that failed before carries AECA/error; once it is processed the label is a lie.
    def _mark_processed(self, facts: EmailFacts) -> None:
        stale = [ERROR] if ERROR in facts.label_ids else []
        self._deps.mailbox.modify(facts.message_id, add=[PROCESSED], remove=stale)

    def _record_failure(self, failure: _Failure) -> AuditLine:
        try:
            attempts = self._deps.store.attempts_increment(failure.message_id)
            if attempts >= MAX_TRIES:
                self._give_up_unfinished_actions(failure.message_id)
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
        return failure if entry is None else replace(failure, progress=self._progress_of(entry))

    # Done before the labels, so that an email Gmail can no longer label still stops being resumed.
    def _give_up_unfinished_actions(self, message_id: str) -> None:
        entry = self._deps.store.journal_get(message_id)
        for action in entry.actions if entry is not None else ():
            record = self._deps.store.action_get(action.key)
            if record is not None and record.status in UNFINISHED_STATUSES:
                self._deps.store.action_update(action.key, ActionStatus.FAILED)


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
    facts, progress = failure.facts, failure.progress
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
        approval_id=None,
        approval_state=None,
        outcome=Outcome.FAILED,
        error=bounded(describe(failure.error), ERROR_TEXT_LIMIT),
        tokens_in=failure.spend.tokens_in,
        tokens_out=failure.spend.tokens_out,
        usd=failure.spend.usd,
    )
