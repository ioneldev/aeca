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
    usd: Decimal
    misses: tuple[EvalMiss, ...]

    def to_json_dict(self) -> dict[str, object]:
        return {
            "per_label": {
                label: {"correct": correct, "total": total}
                for label, (correct, total) in self.per_label.items()
            },
            "confusion": [
                {"expected": expected, "got": got, "count": count}
                for (expected, got), count in self.confusion.items()
            ],
            "urgent_accuracy": self.urgent_accuracy,
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


def evaluate(
    corpus: tuple[CorpusEmail, ...], decide: Decide, *, budget: Budget, clock: Clock
) -> EvalReport:
    """Every email is decided, deliverable or not: the eval needs no Gmail to refuse one."""
    spent_before = budget.spent_usd
    received_at = clock.now()
    answers = [(email, decide(_facts_of(email, received_at=received_at))) for email in corpus]
    return _report(answers, usd=budget.spent_usd - spent_before)


def write_report(report: EvalReport, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report.to_json_dict(), indent=2) + "\n", encoding="utf-8")


def format_report(report: EvalReport) -> str:
    lines = [f"{'label':<16}{'correct':>9}{'accuracy':>10}"]
    for label, (correct, total) in sorted(report.per_label.items()):
        lines.append(f"{label:<16}{f'{correct}/{total}':>9}{_percent(correct, total):>10}")
    lines.append(f"\nurgent accuracy: {report.urgent_accuracy:.0%}")
    lines.append(f"cost: ${report.usd:.4f}")
    if report.misses:
        lines.append("\nmissed:")
        lines.extend(_describe_miss(miss) for miss in report.misses)
    return "\n".join(lines)


def _facts_of(email: CorpusEmail, *, received_at: datetime) -> EmailFacts:
    sender = EVAL_INTERNAL_SENDER if email.sender_role == "internal" else EVAL_EXTERNAL_SENDER
    raw = to_raw(email, sender, received_at=received_at)
    return extract_facts(raw, EVAL_FACT_SETTINGS)


def _report(answers: list[tuple[CorpusEmail, Decision]], *, usd: Decimal) -> EvalReport:
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
    return EvalReport(
        per_label={label: (corrects[label], totals[label]) for label in totals},
        confusion=dict(confusion),
        urgent_accuracy=urgent_right / len(answers) if answers else 1.0,
        usd=usd,
        misses=tuple(misses),
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
    label = f"{miss.expected_label} -> {miss.got_label}"
    urgency = (
        ""
        if miss.expected_urgent == miss.got_urgent
        else (f", urgent expected {miss.expected_urgent} got {miss.got_urgent}")
    )
    return f"  {miss.email_id}: {label}{urgency}"


def _percent(correct: int, total: int) -> str:
    return f"{correct / total:.0%}"
