"""The approval loop: asks the manager by email, reads the answers, and executes once approved.

The inbox loop opens an approval: it is saved with its holds, and the request goes out through the
executor's draft path, so it is sent once. Each tick, the approved approvals are resumed, then the
request thread of every awaiting one is read, oldest message first. Only a reply that provably
comes from the manager counts, code reads its first word, and the first decisive answer wins
(spec §4.5). The model never sees a reply.

Each answer is marked read only once what it decided is saved, so a crash before that reads it
again, and a crash after it does not.
"""

from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from decimal import Decimal

from aeca.application.executor import MAX_EVENT_ATTEMPTS, RECENT_SENT_SEARCHED, Executor
from aeca.application.failures import describe, ends_the_tick
from aeca.application.ports import Calendar, Clock, Mailbox, MessageSummary, OpenApproval, Tracer
from aeca.application.state_ports import AuditLog, StateStore
from aeca.domain.actions import Action, ActionStatus, Verb
from aeca.domain.answers import Answer, AnswerRules, answer_of, why_not_an_answer
from aeca.domain.approval import Approval, ApprovalState
from aeca.domain.approval_messages import help_action, request_action
from aeca.domain.audit import ActionSummary, AuditLine, Outcome, action_summary
from aeca.domain.email import RawEmail
from aeca.domain.errors import EmailError
from aeca.domain.facts import EmailFacts, FactSettings, extract_facts
from aeca.domain.ids import approval_id, event_id
from aeca.domain.labels import AWAITING, DRAFT, PROCESSED
from aeca.domain.planner import (
    AlternativesRequest,
    PlanSettings,
    alternatives_search_span,
    busy_intervals,
    find_alternatives,
    inside_work_hours,
    slot_is_free,
)
from aeca.domain.policy import alternatives_reply, for_attempt, is_bound_to_the_slot
from aeca.domain.time_range import Interval, in_utc

# The first try, then the next two ticks (spec §4.5: retried with the same keys, then failed).
MAX_EXECUTION_TRIES = 3
ANSWERED_BY_THE_MANAGER = "manager"
REFUSED_BY_A_RULE = "rule"
ANSWER_IGNORED = "answer_ignored:"
ANSWER_UNCLEAR = "answer_unclear"
ANSWER_AFTER_THE_DECISION = "answer_after_the_decision"
SLOT_TAKEN = "slot_taken"


@dataclass(frozen=True)
class ApprovalDeps:
    mailbox: Mailbox
    calendar: Calendar
    store: StateStore
    executor: Executor
    audit: AuditLog
    tracer: Tracer
    clock: Clock
    plan_settings: PlanSettings
    answer_rules: AnswerRules
    # MANAGER_ADDRESS, else the account itself: where the request goes.
    manager_address: str
    # The inbox loop's, so an email read again has the facts it was planned with.
    fact_settings: FactSettings


@dataclass(frozen=True, slots=True)
class _Reply:
    """A message of a request thread, read once."""

    raw: RawEmail
    facts: EmailFacts


@dataclass(frozen=True, slots=True)
class _Outcome:
    """What one audit line about an approval says. `about` is the answer it came from, or None
    when no answer is in hand, as when an approved approval is resumed."""

    approval: Approval
    about: EmailFacts | None
    outcome: Outcome
    decided_by: str = ANSWERED_BY_THE_MANAGER
    rules: tuple[str, ...] = ()
    error: str | None = None
    actions: tuple[Action, ...] | None = None


class ApprovalService:
    def __init__(self, deps: ApprovalDeps) -> None:
        self._deps = deps

    # --- opening ---

    def open(self, request: OpenApproval) -> str:
        """For the same source and attempt it returns the approval already opened, whatever its
        state, and only finishes sending its request if a crash stopped it."""
        opened_id = approval_id(request.source.message_id, request.attempt)
        approval = self._deps.store.approval_get(opened_id) or self._saved(request, opened_id)
        self._request_unless_sent(approval, request.source)
        return opened_id

    def _saved(self, request: OpenApproval, opened_id: str) -> Approval:
        approval = Approval(
            id=opened_id,
            source_message_id=request.source.message_id,
            source_thread_id=request.source.thread_id,
            attempt=request.attempt,
            actions=request.actions,
            state=ApprovalState.AWAITING,
            request_thread_id=None,
            summary=request.summary,
            holds=request.holds,
            opened_at=self._deps.clock.now(),
            source_subject=request.source.subject,
        )
        self._deps.store.approval_save(approval)
        return approval

    def _request_unless_sent(self, approval: Approval, source: EmailFacts) -> None:
        if approval.state is ApprovalState.AWAITING and approval.request_thread_id is None:
            self._send_request(approval, source)

    # Built from the saved approval, never from the caller's request, so a resume registers the
    # very action the first run did.
    def _send_request(self, approval: Approval, source: EmailFacts) -> None:
        request = request_action(
            approval,
            to=self._deps.manager_address,
            outside_work_hours=self._outside_work_hours(approval),
        )
        self._deps.store.action_register(request, approval.source_message_id)
        self._deps.executor.run((request,), source)
        thread_id = self._request_thread_of(approval)
        self._deps.store.approval_save(replace(approval, request_thread_id=thread_id))
        self._deps.tracer.event(
            "approval_requested", approval_id=approval.id, request_thread_id=thread_id
        )

    # The executor may have found the request already sent, or only know its draft, so the sent
    # request is looked up by its X-AECA-Id, which is the approval id.
    def _request_thread_of(self, approval: Approval) -> str:
        sent_id = self._deps.mailbox.recently_sent_with_aeca_id(approval.id, RECENT_SENT_SEARCHED)
        if sent_id is None:
            raise EmailError(
                f"the request for approval {approval.id} is not among the recent sent mail",
                next_action="AECA retries on the next poll; see trace.jsonl",
            )
        return self._deps.mailbox.get(sent_id).thread_id

    def _outside_work_hours(self, approval: Approval) -> bool:
        settings = self._deps.plan_settings
        return any(not inside_work_hours(slot, settings) for slot in _event_slots(approval))

    # --- each tick ---

    def process_all(self) -> list[AuditLine]:
        """Resumes the approved approvals, then reads the answers to the awaiting ones.

        A system-wide error is raised. Any other failure belongs to one approval: it is traced,
        and that approval is tried again on the next tick."""
        lines: list[AuditLine] = []
        for approval in self._deps.store.approvals_approved():
            lines.extend(self._guarded(approval, self._resume))
        for approval in self._deps.store.approvals_awaiting():
            lines.extend(self._guarded(approval, self._read_answers))
        return lines

    def _guarded(
        self, approval: Approval, work: Callable[[Approval], list[AuditLine]]
    ) -> list[AuditLine]:
        try:
            return work(approval)
        except Exception as error:
            if ends_the_tick(error):
                raise
            self._deps.tracer.event(
                "approval_not_processed", approval_id=approval.id, error=describe(error)
            )
            return []

    def _resume(self, approval: Approval) -> list[AuditLine]:
        return [self._execute(approval, about=None)]

    def _read_answers(self, approval: Approval) -> list[AuditLine]:
        if approval.request_thread_id is None:
            self._send_request(approval, self._source_facts(approval))
            return []
        lines: list[AuditLine] = []
        for summary in self._unread(approval.request_thread_id):
            # Re-read each time: an earlier answer in this thread may have closed the approval.
            current = self._deps.store.approval_get(approval.id) or approval
            line = self._take(current, self._read(summary))
            if line is not None:
                lines.append(line)
            # An answer left unread is read again next tick, and the ones after it wait behind
            # it: the first decisive answer must stay the first.
            if not self._deps.store.answer_seen(summary.message_id):
                break
        return lines

    # A draft is not an answer yet, and Gmail may keep its message id when it is sent, so it is
    # neither read nor marked seen.
    def _unread(self, request_thread_id: str) -> list[MessageSummary]:
        return [
            summary
            for summary in self._deps.mailbox.thread_summaries(request_thread_id)
            if DRAFT not in summary.label_ids
            and not self._deps.store.answer_seen(summary.message_id)
        ]

    def _read(self, summary: MessageSummary) -> _Reply:
        raw = self._deps.mailbox.get(summary.message_id)
        return _Reply(raw=raw, facts=extract_facts(raw, self._deps.fact_settings))

    def _take(self, approval: Approval, reply: _Reply) -> AuditLine | None:
        if _is_aecas_own(reply.facts):
            self._deps.store.answer_mark_seen(reply.facts.message_id)
            return None
        reason = why_not_an_answer(reply.facts, self._deps.answer_rules)
        if reason is not None:
            return self._ignored(approval, reply, rule=f"{ANSWER_IGNORED}{reason}")
        if approval.state is not ApprovalState.AWAITING:
            return self._ignored(approval, reply, rule=ANSWER_AFTER_THE_DECISION)
        answer = answer_of(reply.raw)
        if answer is Answer.APPROVE:
            return self._on_approve(approval, reply)
        if answer is Answer.REJECT:
            return self._on_reject(approval, reply)
        return self._on_unclear(approval, reply)

    def _ignored(self, approval: Approval, reply: _Reply, *, rule: str) -> AuditLine:
        return self._answered(
            reply,
            _Outcome(
                approval,
                reply.facts,
                Outcome.IGNORED,
                decided_by=REFUSED_BY_A_RULE,
                rules=(rule,),
                actions=(),
            ),
        )

    # --- the answers ---

    def _on_reject(self, approval: Approval, reply: _Reply) -> AuditLine:
        rejected = approval.reject()
        self._close(rejected)
        return self._answered(reply, _Outcome(rejected, reply.facts, Outcome.DONE))

    def _on_unclear(self, approval: Approval, reply: _Reply) -> AuditLine:
        help_reply = help_action(approval)
        self._deps.store.action_register(help_reply, reply.facts.message_id)
        error: str | None = None
        try:
            self._deps.executor.run((help_reply,), reply.facts)
        except EmailError as failed:
            # The approval still waits; the next unclear answer retries the same help reply.
            error = describe(failed)
        return self._answered(
            reply,
            _Outcome(
                approval,
                reply.facts,
                Outcome.AWAITING_APPROVAL,
                rules=(ANSWER_UNCLEAR,),
                error=error,
                actions=(help_reply,),
            ),
        )

    # Review I1: once a successor exists, the supersede was decided. It is finished, never
    # re-checked, or a slot that freed meanwhile would run this approval beside its successor.
    def _on_approve(self, approval: Approval, reply: _Reply) -> AuditLine:
        if self._saved_successor(approval) is not None or self._slot_taken(approval):
            return self._supersede(approval, reply)
        approved = approval.approve()
        self._deps.store.approval_save(approved)
        self._mark_read(reply)
        return self._execute(approved, about=reply.facts)

    # --- executing, exactly once ---

    def _execute(self, approval: Approval, *, about: EmailFacts | None) -> AuditLine:
        try:
            self._deps.executor.run(approval.actions, self._source_facts(approval))
        except EmailError as error:
            return self._execution_failed(approval, about=about, error=error)
        executed = approval.executed()
        self._close(executed)
        return self._audited(_Outcome(executed, about, Outcome.DONE))

    # Approval ids never collide with Gmail message ids, so the attempts counter the inbox keeps
    # per message counts an approval's executions too.
    def _execution_failed(
        self, approval: Approval, *, about: EmailFacts | None, error: EmailError
    ) -> AuditLine:
        tries = self._deps.store.attempts_increment(approval.id)
        if tries >= MAX_EXECUTION_TRIES:
            approval = approval.failed()
            self._close(approval)
        return self._audited(_Outcome(approval, about, Outcome.FAILED, error=describe(error)))

    # --- the re-check on APPROVE (spec §4.4) ---

    def _slot_taken(self, approval: Approval) -> bool:
        return any(not self._still_free(slot, approval) for slot in _event_slots(approval))

    def _still_free(self, slot: Interval, approval: Approval) -> bool:
        if in_utc(slot.start) <= in_utc(self._deps.clock.now()):
            return False
        busy = self._busy_around(slot, approval)
        return slot_is_free(slot, busy, buffer_minutes=self._deps.plan_settings.buffer_minutes)

    # Blocking case 3: the approval's own hold is not busy, or it would supersede itself on every
    # poll. Holds of the source thread count as free for it, as in the plan (spec §4.4), and so
    # does an event under one of the ids AECA derives for this email: it is AECA's own work.
    def _busy_around(self, span: Interval, approval: Approval) -> tuple[Interval, ...]:
        buffer = timedelta(minutes=self._deps.plan_settings.buffer_minutes)
        window = Interval(start=in_utc(span.start) - buffer, end=in_utc(span.end) + buffer)
        own_ids = _own_event_ids(approval)
        events = [event for event in self._deps.calendar.events(window) if event.id not in own_ids]
        holds = [
            hold
            for hold in self._deps.store.holds_active(self._deps.clock.now())
            if hold.approval_id != approval.id
        ]
        return busy_intervals(events, holds, approval.source_thread_id)

    # The decision is saved before any mail goes: the successor, then this approval superseded,
    # then its audit line and the answer read, and only then the successor's request. Interrupted
    # anywhere, the next tick finishes the same supersede.
    def _supersede(self, approval: Approval, reply: _Reply) -> AuditLine:
        try:
            source = self._source_facts(approval)
        except EmailError as error:
            return self._source_unreadable(approval, reply, error)
        successor = self._saved_successor(approval) or self._new_successor(approval, source)
        superseded = approval.supersede()
        if successor is None:
            self._close(superseded)
        else:
            # Its holds go; the email stays awaiting, now for the successor.
            self._deps.store.approval_save(superseded)
        outcome = Outcome.DONE if successor is None else Outcome.AWAITING_APPROVAL
        line = self._answered(
            reply, _Outcome(superseded, reply.facts, outcome, rules=(SLOT_TAKEN,))
        )
        if successor is not None:
            self._request_unless_sent(successor, source)
        return line

    def _saved_successor(self, approval: Approval) -> Approval | None:
        return self._deps.store.approval_get(_successor_id(approval))

    def _new_successor(self, approval: Approval, source: EmailFacts) -> Approval | None:
        attempt = approval.attempt + 1
        source_id = approval.source_message_id
        alternatives = self._alternatives(approval)
        offered = (
            alternatives_reply(
                source_id,
                alternatives,
                attempt=attempt,
                urgent_quote=_urgent_quote_of(approval),
            ),
        )
        carried = tuple(
            for_attempt(action, source_message_id=source_id, attempt=attempt)
            for action in approval.actions
            if not is_bound_to_the_slot(action)
        )
        actions = (*(offered if alternatives else ()), *carried)
        if not actions:
            return None
        request = OpenApproval(
            source=source,
            actions=actions,
            holds=alternatives,
            summary=approval.summary,
            attempt=attempt,
        )
        return self._saved(request, _successor_id(approval))

    # Review X2: an approval whose source email can no longer be read is never blocked forever.
    # The answer stays unread for the next two ticks, then the approval fails and is closed.
    def _source_unreadable(self, approval: Approval, reply: _Reply, error: EmailError) -> AuditLine:
        tries = self._deps.store.attempts_increment(approval.id)
        if tries < MAX_EXECUTION_TRIES:
            return self._audited(
                _Outcome(approval, reply.facts, Outcome.FAILED, error=describe(error))
            )
        # The manager approved; it is carrying out that answer which failed.
        failed = approval.approve().failed()
        self._close(failed)
        return self._answered(
            reply, _Outcome(failed, reply.facts, Outcome.FAILED, error=describe(error))
        )

    def _alternatives(self, approval: Approval) -> tuple[Interval, ...]:
        now = self._deps.clock.now()
        settings = self._deps.plan_settings
        slots = _event_slots(approval)
        duration = in_utc(slots[0].end) - in_utc(slots[0].start)
        busy = self._busy_around(alternatives_search_span(now, settings), approval)
        return find_alternatives(
            AlternativesRequest(now=now, busy=busy, duration=duration, settings=settings)
        )

    # --- closing ---

    # The label goes first: a system-wide error here leaves the approval open, so the next tick
    # closes it again. A label Gmail refuses for this email alone does not keep it open forever.
    def _close(self, closed: Approval) -> None:
        try:
            self._deps.mailbox.modify(closed.source_message_id, add=[], remove=[AWAITING])
        except EmailError as error:
            self._deps.tracer.event(
                "awaiting_label_not_removed", approval_id=closed.id, error=describe(error)
            )
        self._deps.store.approval_save(closed)

    # Seen first: if the label then fails, the inbox loop labels it later without auditing it a
    # second time, since it knows the approval loop already read it.
    def _mark_read(self, reply: _Reply) -> None:
        self._deps.store.answer_mark_seen(reply.facts.message_id)
        self._deps.mailbox.modify(reply.facts.message_id, add=[PROCESSED], remove=[])

    def _source_facts(self, approval: Approval) -> EmailFacts:
        raw = self._deps.mailbox.get(approval.source_message_id)
        return extract_facts(raw, self._deps.fact_settings)

    # --- the audit ---

    # The line comes before the label, as for an inbox email: a label that fails leaves the
    # answer unread, never unaudited.
    def _answered(self, reply: _Reply, outcome: _Outcome) -> AuditLine:
        line = self._audited(outcome)
        self._mark_read(reply)
        return line

    def _audited(self, outcome: _Outcome) -> AuditLine:
        line = self._line(outcome)
        self._deps.audit.write(line)
        return line

    def _line(self, outcome: _Outcome) -> AuditLine:
        approval, about = outcome.approval, outcome.about
        actions = approval.actions if outcome.actions is None else outcome.actions
        return AuditLine(
            time=self._deps.clock.now(),
            message_id=about.message_id if about else approval.source_message_id,
            thread_id=about.thread_id if about else approval.source_thread_id,
            sender=about.sender if about else "",
            subject=about.subject if about else approval.source_subject,
            label="",
            urgent=False,
            decided_by=outcome.decided_by,
            rules=outcome.rules,
            actions=tuple(self._summary_of(action) for action in actions),
            approval_id=approval.id,
            approval_state=approval.state.value,
            outcome=outcome.outcome,
            error=outcome.error,
            tokens_in=0,
            tokens_out=0,
            usd=Decimal(0),
        )

    def _summary_of(self, action: Action) -> ActionSummary:
        record = self._deps.store.action_get(action.key)
        return action_summary(action, ActionStatus.PENDING if record is None else record.status)


def _successor_id(approval: Approval) -> str:
    return approval_id(approval.source_message_id, approval.attempt + 1)


def _is_aecas_own(facts: EmailFacts) -> bool:
    return facts.aeca_kind is not None and facts.sent_by_account


def _own_event_ids(approval: Approval) -> frozenset[str]:
    return frozenset(
        event_id(approval.source_message_id, attempt) for attempt in range(MAX_EVENT_ATTEMPTS)
    )


def _event_slots(approval: Approval) -> tuple[Interval, ...]:
    return tuple(
        Interval(
            start=datetime.fromisoformat(action.args["start"]),
            end=datetime.fromisoformat(action.args["end"]),
        )
        for action in approval.actions
        if action.verb is Verb.CREATE_EVENT
    )


def _urgent_quote_of(approval: Approval) -> str | None:
    return next(
        (action.args["quote"] for action in approval.actions if "quote" in action.args), None
    )
