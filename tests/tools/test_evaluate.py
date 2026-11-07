import json
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

from fakes.fixed_clock import FixedClock
from support.corpus import make_corpus_email

from aeca.application.budget import Budget
from aeca.application.ports import PaidCall, Usage
from aeca.domain.decision import Decision
from aeca.domain.facts import EmailFacts
from aeca.domain.labels import Label
from aeca.tools.corpus import CorpusEmail
from aeca.tools.evaluate import EvalReport, evaluate, format_report, write_report


def expecting(email_id: str, label: str, *, urgent: bool = False) -> CorpusEmail:
    expected = {
        "label": label,
        "urgent": urgent,
        "actions": [],
        "approval": False,
        "reply": "none",
        "calendar": None,
    }
    return make_corpus_email(id=email_id, subject=email_id, expected=expected)


class ScriptedDecide:
    """Answers by subject, and pays COST for each call as the real decision would."""

    def __init__(self, budget: Budget, answers: dict[str, tuple[Label, bool]]) -> None:
        self._budget = budget
        self._answers = answers
        self.seen: list[EmailFacts] = []

    def __call__(self, facts: EmailFacts) -> Decision:
        self.seen.append(facts)
        label, urgent = self._answers[facts.subject]
        self._budget.charge(
            PaidCall(Usage(1, 1, 0, 0), model="m", cost_usd=Decimal("0.02"), latency_s=0.1)
        )
        return replace(Decision.fallback("scripted"), label=label, urgent=urgent)


def run(
    corpus: tuple[CorpusEmail, ...], answers: dict[str, tuple[Label, bool]]
) -> tuple[EvalReport, ScriptedDecide]:
    budget = Budget(Decimal(5))
    decide = ScriptedDecide(budget, answers)
    return evaluate(corpus, decide, budget=budget, clock=FixedClock()), decide


def test_eval_reports_accuracy_per_label() -> None:
    corpus = (
        expecting("a", "TASK"),
        expecting("b", "TASK"),
        expecting("c", "SPAM"),
    )
    report, _ = run(
        corpus,
        {
            "a": (Label.TASK, False),
            "b": (Label.INFO_ONLY, False),
            "c": (Label.SPAM, False),
        },
    )

    assert report.per_label == {"TASK": (1, 2), "SPAM": (1, 1)}


def test_eval_counts_the_confusion_of_expected_against_got() -> None:
    corpus = (expecting("a", "TASK"), expecting("b", "TASK"), expecting("c", "SPAM"))
    report, _ = run(
        corpus,
        {"a": (Label.INFO_ONLY, False), "b": (Label.INFO_ONLY, False), "c": (Label.SPAM, False)},
    )

    assert report.confusion == {("TASK", "INFO_ONLY"): 2, ("SPAM", "SPAM"): 1}


def test_eval_scores_urgency_over_every_email() -> None:
    corpus = (
        expecting("a", "TASK", urgent=True),
        expecting("b", "TASK", urgent=False),
        expecting("c", "TASK", urgent=False),
        expecting("d", "TASK", urgent=True),
    )
    report, _ = run(
        corpus,
        {
            "a": (Label.TASK, True),
            "b": (Label.TASK, True),
            "c": (Label.TASK, False),
            "d": (Label.TASK, False),
        },
    )

    assert report.urgent_accuracy == 0.5


def test_eval_lists_the_emails_it_got_wrong() -> None:
    corpus = (expecting("a", "TASK"), expecting("b", "SPAM", urgent=True))
    report, _ = run(corpus, {"a": (Label.TASK, False), "b": (Label.SPAM, False)})

    assert [(miss.email_id, miss.expected_urgent, miss.got_urgent) for miss in report.misses] == [
        ("b", True, False)
    ]


def test_eval_reports_what_the_budget_spent_and_nothing_before_it() -> None:
    budget = Budget(Decimal(5))
    budget.charge(PaidCall(Usage(1, 1, 0, 0), model="m", cost_usd=Decimal("1"), latency_s=0.1))
    decide = ScriptedDecide(budget, {"a": (Label.TASK, False), "b": (Label.TASK, False)})

    report = evaluate(
        (expecting("a", "TASK"), expecting("b", "TASK")),
        decide,
        budget=budget,
        clock=FixedClock(),
    )

    assert report.usd == Decimal("0.04")


def test_eval_decides_on_facts_read_from_the_email_as_gmail_would_deliver_it() -> None:
    internal = make_corpus_email(
        id="boss", subject="boss", sender_role="internal", expected=expecting("x", "TASK").expected
    )
    _, decide = run((internal,), {"boss": (Label.TASK, False)})

    assert decide.seen[0].internal is True and decide.seen[0].auth.dmarc == "pass"


def test_eval_decides_every_email_including_the_undeliverable_ones() -> None:
    corpus = (
        make_corpus_email(
            id="n", subject="n", deliverable=False, expected=expecting("x", "SPAM").expected
        ),
    )
    _, decide = run(corpus, {"n": (Label.SPAM, False)})

    assert len(decide.seen) == 1


def test_the_report_prints_as_a_table_with_label_percentages_and_cost() -> None:
    corpus = (expecting("a", "TASK"), expecting("b", "TASK"))
    report, _ = run(corpus, {"a": (Label.TASK, False), "b": (Label.INFO_ONLY, False)})

    text = format_report(report)

    assert "TASK" in text and "1/2" in text and "50%" in text
    assert "$0.04" in text
    assert "b" in text and "INFO_ONLY" in text


def test_the_report_is_written_as_json_with_the_confusion_spelled_out(tmp_path: Path) -> None:
    corpus = (expecting("a", "TASK"),)
    report, _ = run(corpus, {"a": (Label.INFO_ONLY, False)})
    path = tmp_path / "nested" / "eval.json"

    write_report(report, path)

    written = json.loads(path.read_text())
    assert written["per_label"] == {"TASK": {"correct": 0, "total": 1}}
    assert written["confusion"] == [{"expected": "TASK", "got": "INFO_ONLY", "count": 1}]
    assert written["usd"] == "0.02"
