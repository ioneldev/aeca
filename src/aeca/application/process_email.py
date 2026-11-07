"""One inbox email, from a fresh read to its label, its audit line and AECA/processed.

This is the thin path: it applies the label the decision names, and nothing else yet.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from aeca.application.budget import Budget, Spend
from aeca.application.decide import BudgetExhausted
from aeca.application.ports import Clock, Mailbox, ProviderError, Tracer
from aeca.application.state_ports import AuditLog, StateStore
from aeca.domain.audit import ActionSummary, AuditLine, Outcome
from aeca.domain.decision import Decision
from aeca.domain.errors import AecaError, EmailError, SystemWideError
from aeca.domain.facts import EmailFacts, FactSettings, extract_facts
from aeca.domain.labels import (
    ERROR,
    FINANCE_LABEL,
    PROCESSED,
    URGENT_LABEL,
    Label,
)
from aeca.domain.text import bounded

MAX_TRIES = 3
ERROR_TEXT_LIMIT = 300
STARRED = "STARRED"
SPAM_SYSTEM_LABEL = "SPAM"


@dataclass(frozen=True)
class InboxDeps:
    mailbox: Mailbox
    store: StateStore
    audit: AuditLog
    tracer: Tracer
    decide: Callable[[EmailFacts], Decision]
    facts: FactSettings
    clock: Clock
    # The decision pays for its own model calls, and the budget is where that shows.
    budget: Budget


@dataclass(frozen=True, slots=True)
class _Classified:
    facts: EmailFacts
    decision: Decision
    applied_labels: tuple[str, ...]
    spend: Spend


@dataclass(frozen=True, slots=True)
class _Failure:
    message_id: str
    facts: EmailFacts | None
    error: Exception
    spend: Spend


def ends_the_tick(error: Exception) -> bool:
    """A failure every email would share, so no email is charged for it."""
    if isinstance(error, ProviderError):
        return error.system_wide
    return isinstance(error, SystemWideError | BudgetExhausted)


class InboxProcessor:
    def __init__(self, deps: InboxDeps) -> None:
        self._deps = deps

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
            known_facts = extract_facts(raw, self._deps.facts)
            return self._process_unprocessed(known_facts, spend_before)
        except Exception as error:
            if ends_the_tick(error):
                raise
            spend = self._deps.budget.spent - spend_before
            return self._record_failure(_Failure(message_id, known_facts, error, spend))

    def _process_unprocessed(self, facts: EmailFacts, spend_before: Spend) -> AuditLine | None:
        if self._is_skipped(facts):
            return None
        decision = self._deps.decide(facts)
        labels = labels_to_add(decision)
        self._deps.mailbox.modify(facts.message_id, add=labels, remove=[])
        spend = self._deps.budget.spent - spend_before
        line = _done_line(self._deps.clock.now(), _Classified(facts, decision, labels, spend))
        self._deps.audit.write(line)
        self._mark_processed(facts)
        return line

    def _is_skipped(self, facts: EmailFacts) -> bool:
        if facts.aeca_kind is not None and facts.sent_by_account:
            self._mark_processed(facts)
            return True
        return self._deps.store.approval_by_request_thread(facts.thread_id) is not None

    # An email that failed before carries AECA/error; once it is processed the label is a lie.
    def _mark_processed(self, facts: EmailFacts) -> None:
        stale = [ERROR] if ERROR in facts.label_ids else []
        self._deps.mailbox.modify(facts.message_id, add=[PROCESSED], remove=stale)

    def _record_failure(self, failure: _Failure) -> AuditLine:
        try:
            attempts = self._deps.store.attempts_increment(failure.message_id)
            line = _failed_line(self._deps.clock.now(), failure)
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
                f"{_describe(recording_error)}",
                next_action="Check the data volume and the Gmail account, then wait for the retry",
            ) from recording_error
        return line


def _done_line(now: datetime, classified: _Classified) -> AuditLine:
    facts, decision, spend = classified.facts, classified.decision, classified.spend
    actions: tuple[ActionSummary, ...] = tuple(
        ("label", name, Outcome.DONE) for name in classified.applied_labels
    )
    return AuditLine(
        time=now,
        message_id=facts.message_id,
        thread_id=facts.thread_id,
        sender=facts.sender,
        subject=facts.subject,
        label=decision.label.value,
        urgent=decision.urgent,
        decided_by=decision.decided_by.value,
        rules=decision.rules_fired,
        actions=actions,
        approval_id=None,
        approval_state=None,
        outcome=Outcome.DONE,
        error=None,
        tokens_in=spend.tokens_in,
        tokens_out=spend.tokens_out,
        usd=spend.usd,
    )


def _failed_line(now: datetime, failure: _Failure) -> AuditLine:
    facts = failure.facts
    return AuditLine(
        time=now,
        message_id=failure.message_id,
        thread_id=facts.thread_id if facts else "",
        sender=facts.sender if facts else "",
        subject=facts.subject if facts else "",
        label="",
        urgent=False,
        decided_by="",
        rules=(),
        actions=(),
        approval_id=None,
        approval_state=None,
        outcome=Outcome.FAILED,
        error=bounded(_describe(failure.error), ERROR_TEXT_LIMIT),
        tokens_in=failure.spend.tokens_in,
        tokens_out=failure.spend.tokens_out,
        usd=failure.spend.usd,
    )


def labels_to_add(decision: Decision) -> tuple[str, ...]:
    labels = [SPAM_SYSTEM_LABEL if decision.label is Label.SPAM else decision.label.value]
    if decision.urgent:
        labels += [STARRED, URGENT_LABEL]
    if decision.finance:
        labels.append(FINANCE_LABEL)
    return tuple(labels)


def _describe(error: Exception) -> str:
    if isinstance(error, AecaError):
        return str(error)
    return f"{type(error).__name__}: {error}"
