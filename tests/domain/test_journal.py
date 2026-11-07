"""The decision a journal entry records is what a resume works from: it must come back whole."""

from datetime import datetime
from zoneinfo import ZoneInfo

from support.decisions import decision_of

from aeca.domain.journal import JournalledDecision
from aeca.domain.labels import Label, ReplyKind
from aeca.domain.time_range import Interval

BUCHAREST = ZoneInfo("Europe/Bucharest")
SLOT = Interval(
    datetime(2026, 11, 10, 15, 0, tzinfo=BUCHAREST),
    datetime(2026, 11, 10, 15, 30, tzinfo=BUCHAREST),
)


def test_a_journalled_decision_comes_back_from_its_json() -> None:
    decision = decision_of(
        Label.TASK,
        urgent=True,
        urgent_quote="today",
        reply=ReplyKind.ACKNOWLEDGEMENT,
        rules_fired=("a_rule",),
    )
    journalled = JournalledDecision.of(decision, holds=(SLOT,), notes=("a_note",))

    assert JournalledDecision.from_json(journalled.to_json()) == journalled


def test_it_keeps_what_the_audit_line_and_a_reopened_approval_need() -> None:
    journalled = JournalledDecision.of(decision_of(Label.MARKETING), holds=(SLOT,), notes=())

    reread = JournalledDecision.from_json(journalled.to_json())

    assert (reread.label, reread.urgent, reread.decided_by) == ("MARKETING", False, "model")
    assert (reread.summary, reread.holds) == (decision_of(Label.MARKETING).summary, (SLOT,))
