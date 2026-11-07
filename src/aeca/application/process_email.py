"""One inbox email, from a fresh read to its label, its audit line and AECA/processed.

This is the thin path: it applies the label the decision names, and nothing else yet.
"""

from collections.abc import Callable
from dataclasses import dataclass

from aeca.application.budget import Budget, Spend
from aeca.application.decide import BudgetExhausted
from aeca.application.ports import Clock, Mailbox, ProviderError, Tracer
from aeca.application.state_ports import AuditLog, StateStore
from aeca.domain.audit import ActionSummary, AuditLine
from aeca.domain.decision import Decision
from aeca.domain.errors import EmailError, SystemWideError
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
DONE = "done"
FAILED = "failed"


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


class InboxProcessor:
    def __init__(self, deps: InboxDeps) -> None:
        self._deps = deps

    def process(self, message_id: str) -> AuditLine | None:
        """None means the email is not AECA's to classify: it was skipped.

        A system-wide error is raised to the caller, and the email is charged nothing for it."""
        spend_before = self._deps.budget.spent
        known_facts: EmailFacts | None = None
        try:
            raw = self._deps.mailbox.get(message_id)
            if PROCESSED in raw.label_ids:
                return None
            known_facts = extract_facts(raw, self._deps.facts)
            return self._process_unprocessed(known_facts, spend_before)
        except (SystemWideError, BudgetExhausted):
            raise
        except ProviderError as error:
            if error.system_wide:
                raise
            return self._record_failure(message_id, known_facts, error, spend_before)
        except Exception as error:
            return self._record_failure(message_id, known_facts, error, spend_before)

    def _process_unprocessed(self, facts: EmailFacts, spend_before: Spend) -> AuditLine | None:
        if self._is_skipped(facts):
            return None
        decision = self._deps.decide(facts)
        applied = self._apply(facts.message_id, decision)
        line = self._line_of(facts, decision, applied, self._deps.budget.spent - spend_before)
        self._deps.audit.write(line)
        self._deps.mailbox.modify(facts.message_id, add=[PROCESSED], remove=[])
        return line

    def _is_skipped(self, facts: EmailFacts) -> bool:
        if facts.aeca_kind is not None and facts.sent_by_account:
            self._deps.mailbox.modify(facts.message_id, add=[PROCESSED], remove=[])
            return True
        return self._deps.store.approval_by_request_thread(facts.thread_id) is not None

    def _apply(self, message_id: str, decision: Decision) -> tuple[str, ...]:
        labels = labels_to_add(decision)
        self._deps.mailbox.modify(message_id, add=labels, remove=[])
        return labels

    def _line_of(
        self, facts: EmailFacts, decision: Decision, applied: tuple[str, ...], spend: Spend
    ) -> AuditLine:
        actions: tuple[ActionSummary, ...] = tuple(("label", name, DONE) for name in applied)
        return AuditLine(
            time=self._deps.clock.now(),
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
            outcome=DONE,
            error=None,
            tokens_in=spend.tokens_in,
            tokens_out=spend.tokens_out,
            usd=spend.usd,
        )

    def _record_failure(
        self,
        message_id: str,
        facts: EmailFacts | None,
        error: Exception,
        spend_before: Spend,
    ) -> AuditLine:
        attempts = self._deps.store.attempts_increment(message_id)
        labels = [ERROR, PROCESSED] if attempts >= MAX_TRIES else [ERROR]
        self._deps.mailbox.modify(message_id, add=labels, remove=[])
        self._deps.tracer.event(
            "email_failed", message_id=message_id, attempts=attempts, error=repr(error)
        )
        line = self._failed_line(message_id, facts, error, self._deps.budget.spent - spend_before)
        self._deps.audit.write(line)
        return line

    def _failed_line(
        self, message_id: str, facts: EmailFacts | None, error: Exception, spend: Spend
    ) -> AuditLine:
        return AuditLine(
            time=self._deps.clock.now(),
            message_id=message_id,
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
            outcome=FAILED,
            error=bounded(_describe(error), ERROR_TEXT_LIMIT),
            tokens_in=spend.tokens_in,
            tokens_out=spend.tokens_out,
            usd=spend.usd,
        )


def labels_to_add(decision: Decision) -> tuple[str, ...]:
    labels = [SPAM_SYSTEM_LABEL if decision.label is Label.SPAM else decision.label.value]
    if decision.urgent:
        labels += [STARRED, URGENT_LABEL]
    if decision.finance:
        labels.append(FINANCE_LABEL)
    return tuple(labels)


def _describe(error: Exception) -> str:
    if isinstance(error, EmailError | ProviderError):
        return str(error)
    return f"{type(error).__name__}: {error}"
