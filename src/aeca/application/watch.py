"""Turns what the service sees into incidents: a refused key, the cost cap, and emails failing
in a row. The incident's open state is in the store; only the count of failures is in memory."""

from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol

from aeca.application.decide import BudgetExhausted
from aeca.application.failures import describe
from aeca.application.incidents import IncidentKind, IncidentReporter
from aeca.application.ports import ProviderError, Tracer
from aeca.domain.errors import AecaError
from aeca.domain.text import bounded

KEY_REFUSED_KINDS = frozenset({"key_refused", "authentication", "permission"})
LATEST_ERROR_LIMIT = 300


class SpendGate(Protocol):
    @property
    def spent_usd(self) -> Decimal: ...

    def exhausted(self) -> bool: ...


@dataclass(frozen=True)
class WatchDeps:
    incidents: IncidentReporter
    budget: SpendGate
    tracer: Tracer
    alert_after_failures: int


class FailureWatch:
    def __init__(self, deps: WatchDeps) -> None:
        self._deps = deps
        # Emails, not attempts: one email retried on every poll is one failure.
        self._failed_in_a_row: dict[str, str] = {}
        self._model_answered = False

    def email_failed(self, message_id: str, error: str) -> None:
        self._failed_in_a_row[message_id] = error
        if len(self._failed_in_a_row) >= self._deps.alert_after_failures:
            self._report(IncidentKind.FAILURES_IN_A_ROW, self._failures_detail(error))

    def email_processed(self, *, used_model: bool) -> None:
        self._model_answered = self._model_answered or used_model
        self._failed_in_a_row.clear()
        self._deps.incidents.clear(IncidentKind.FAILURES_IN_A_ROW)

    def tick_ended_by(self, error: Exception) -> None:
        if isinstance(error, BudgetExhausted):
            self._report(IncidentKind.COST_CAP, describe(error))
        elif isinstance(error, ProviderError) and error.kind in KEY_REFUSED_KINDS:
            self._report(IncidentKind.KEY_REFUSED, describe(error))

    def tick_completed(self) -> None:
        self._settle_the_cost_cap()
        # Only a paid call proves the key: a rule-decided email never reaches the model.
        if self._model_answered:
            self._deps.incidents.clear(IncidentKind.KEY_REFUSED)
        self._model_answered = False

    # The last call can overshoot the cap without any call being refused, so the manager hears
    # of it when the cap is hit, not when the next email arrives.
    def _settle_the_cost_cap(self) -> None:
        budget = self._deps.budget
        if budget.exhausted():
            self._report(IncidentKind.COST_CAP, f"Spent today: ${budget.spent_usd:.4f}")
        else:
            self._deps.incidents.clear(IncidentKind.COST_CAP)

    def _failures_detail(self, latest_error: str) -> str:
        return (
            f"{len(self._failed_in_a_row)} emails in a row failed. "
            f"The latest error: {bounded(latest_error, LATEST_ERROR_LIMIT)}"
        )

    # A report that cannot be sent must not fail the tick: the same fault reports again next time.
    def _report(self, kind: IncidentKind, detail: str) -> None:
        try:
            self._deps.incidents.report(kind, detail)
        except AecaError as error:
            self._deps.tracer.event("alert_failed", incident=kind.value, error=describe(error))
