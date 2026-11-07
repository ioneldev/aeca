"""Runs the real decision over every corpus email, with no Gmail, and scores it against the
expected answers."""

import json
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from aeca.application.budget import Budget
from aeca.application.ports import Clock
from aeca.domain.decision import Decision
from aeca.domain.errors import AecaError
from aeca.domain.facts import EmailFacts, FactSettings, extract_facts
from aeca.tools.corpus import (
    EVAL_ACCOUNT_ADDRESS,
    EVAL_EXTERNAL_SENDER,
    EVAL_INTERNAL_SENDER,
    CorpusEmail,
    to_raw,
)

Decide = Callable[[EmailFacts], Decision]

EVAL_FACT_SETTINGS = FactSettings(
    account_address=EVAL_ACCOUNT_ADDRESS,
    internal_senders=frozenset({EVAL_INTERNAL_SENDER}),
    bulk_senders=frozenset(),
)


@dataclass(frozen=True, slots=True)
class EvalMiss:
    email_id: str
    expected_label: str
    got_label: str
    expected_urgent: bool
    got_urgent: bool


@dataclass(frozen=True, slots=True)
class EvalReport:
    per_label: dict[str, tuple[int, int]]
    confusion: dict[tuple[str, str], int]
    urgent_accuracy: float
    urgent_caught: int
    urgent_expected: int
    urgent_predicted: int
    usd: Decimal
    misses: tuple[EvalMiss, ...]
    stopped_at: str | None = None
    error_type: str | None = None

    @property
    def partial(self) -> bool:
        return self.stopped_at is not None

    @property
    def urgent_recall(self) -> float | None:
        return _ratio(self.urgent_caught, self.urgent_expected)

    @property
    def urgent_precision(self) -> float | None:
        return _ratio(self.urgent_caught, self.urgent_predicted)

    def to_json_dict(self) -> dict[str, object]:
        return {
            "partial": self.partial,
            "stopped_at": self.stopped_at,
            "error_type": self.error_type,
            "per_label": {
                label: {"correct": correct, "total": total}
                for label, (correct, total) in self.per_label.items()
            },
            "confusion": [
                {"expected": expected, "got": got, "count": count}
                for (expected, got), count in self.confusion.items()
            ],
            "urgent_accuracy": self.urgent_accuracy,
            "urgent": {
                "accuracy": self.urgent_accuracy,
                "recall": {"caught": self.urgent_caught, "of": self.urgent_expected},
                "precision": {"caught": self.urgent_caught, "of": self.urgent_predicted},
            },
            "usd": str(self.usd),
            "misses": [
                {
                    "id": miss.email_id,
                    "expected_label": miss.expected_label,
                    "got_label": miss.got_label,
                    "expected_urgent": miss.expected_urgent,
                    "got_urgent": miss.got_urgent,
                }
                for miss in self.misses
            ],
        }


class EvalStopped(AecaError):
    """The eval failed part way. `report` holds every answer already paid for."""

    def __init__(self, report: EvalReport, *, cause: AecaError) -> None:
        answered = sum(total for _, total in report.per_label.values())
        super().__init__(
            f"The eval stopped at {report.stopped_at!r} after {answered} answers: "
            f"{type(cause).__name__}: {cause}",
            next_action=cause.next_action,
        )
        self.report = report


class ProgressLog:
    """One line per answer, written as it arrives, so a crash loses nothing already paid for."""

    def __init__(self, path: Path) -> None:
        self._path = path
        path.parent.mkdir(parents=True, exist_ok=True)

    def __call__(self, email: CorpusEmail, decision: Decision) -> None:
        line = json.dumps(
            {
                "id": email.id,
                "expected": email.expected.label.value,
                "got": decision.label.value,
                "expected_urgent": email.expected.urgent,
                "got_urgent": decision.urgent,
            }
        )
        with self._path.open("a", encoding="utf-8") as file:
            file.write(line + "\n")

    def discard(self) -> None:
        self._path.unlink(missing_ok=True)


def evaluate(
    corpus: tuple[CorpusEmail, ...],
    decide: Decide,
    *,
    budget: Budget,
    clock: Clock,
    on_decided: Callable[[CorpusEmail, Decision], None] = lambda _email, _decision: None,
) -> EvalReport:
    """Every email is decided, deliverable or not: the eval needs no Gmail to refuse one.

    A failure of the decision stops the eval with EvalStopped, which carries the partial report.
    """
    spent_before = budget.spent_usd
    received_at = clock.now()
    answers: list[tuple[CorpusEmail, Decision]] = []
    for email in corpus:
        try:
            decision = decide(_facts_of(email, received_at=received_at))
        except AecaError as error:
            partial = _report(
                answers,
                usd=budget.spent_usd - spent_before,
                stop=(email.id, type(error).__name__),
            )
            raise EvalStopped(partial, cause=error) from error
        answers.append((email, decision))
        on_decided(email, decision)
    return _report(answers, usd=budget.spent_usd - spent_before)


def write_report(report: EvalReport, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report.to_json_dict(), indent=2) + "\n", encoding="utf-8")


def format_report(report: EvalReport) -> str:
    lines = [f"{'label':<16}{'correct':>9}{'accuracy':>10}"]
    for label, (correct, total) in sorted(report.per_label.items()):
        lines.append(f"{label:<16}{f'{correct}/{total}':>9}{_percent(correct, total):>10}")
    lines.append(f"\nurgent: {_urgent_summary(report)}")
    lines.append(f"cost: ${report.usd:.4f}")
    if report.misses:
        lines.append("\nmissed:")
        lines.extend(_describe_miss(miss) for miss in report.misses)
    if report.partial:
        lines.insert(0, f"PARTIAL: stopped at {report.stopped_at!r} ({report.error_type})\n")
    return "\n".join(lines)


def _urgent_summary(report: EvalReport) -> str:
    return (
        f"accuracy {report.urgent_accuracy:.0%}, "
        f"recall {report.urgent_caught}/{report.urgent_expected}, "
        f"precision {report.urgent_caught}/{report.urgent_predicted}"
    )


def _facts_of(email: CorpusEmail, *, received_at: datetime) -> EmailFacts:
    sender = EVAL_INTERNAL_SENDER if email.sender_role == "internal" else EVAL_EXTERNAL_SENDER
    raw = to_raw(email, sender, received_at=received_at)
    return extract_facts(raw, EVAL_FACT_SETTINGS)


def _report(
    answers: list[tuple[CorpusEmail, Decision]],
    *,
    usd: Decimal,
    stop: tuple[str, str] | None = None,
) -> EvalReport:
    totals: Counter[str] = Counter()
    corrects: Counter[str] = Counter()
    confusion: Counter[tuple[str, str]] = Counter()
    misses: list[EvalMiss] = []
    for email, decision in answers:
        expected, got = email.expected.label.value, decision.label.value
        totals[expected] += 1
        corrects[expected] += expected == got
        confusion[(expected, got)] += 1
        if expected != got or email.expected.urgent != decision.urgent:
            misses.append(_miss(email, decision))
    urgent_right = sum(email.expected.urgent == decision.urgent for email, decision in answers)
    stopped_at, error_type = stop if stop else (None, None)
    return EvalReport(
        per_label={label: (corrects[label], totals[label]) for label in totals},
        confusion=dict(confusion),
        urgent_accuracy=urgent_right / len(answers) if answers else 1.0,
        urgent_caught=sum(e.expected.urgent and d.urgent for e, d in answers),
        urgent_expected=sum(e.expected.urgent for e, _ in answers),
        urgent_predicted=sum(d.urgent for _, d in answers),
        usd=usd,
        misses=tuple(misses),
        stopped_at=stopped_at,
        error_type=error_type,
    )


def _miss(email: CorpusEmail, decision: Decision) -> EvalMiss:
    return EvalMiss(
        email_id=email.id,
        expected_label=email.expected.label.value,
        got_label=decision.label.value,
        expected_urgent=email.expected.urgent,
        got_urgent=decision.urgent,
    )


def _describe_miss(miss: EvalMiss) -> str:
    description = f"  {miss.email_id}: {miss.expected_label} -> {miss.got_label}"
    if miss.expected_urgent != miss.got_urgent:
        description += f", urgent expected {miss.expected_urgent} got {miss.got_urgent}"
    return description


def _ratio(part: int, whole: int) -> float | None:
    return part / whole if whole else None


def _percent(correct: int, total: int) -> str:
    return f"{correct / total:.0%}"
