"""Builds validated decisions for tests that care about what happens after the decision."""

from aeca.domain.decision import DecidedBy, Decision, Meeting
from aeca.domain.labels import Label, ReplyKind

SUMMARY = "Asks for the Q3 numbers."


def decision_of(
    label: Label,
    *,
    urgent: bool = False,
    urgent_quote: str | None = None,
    finance: bool = False,
    reply: ReplyKind = ReplyKind.NONE,
    urgent_draft: str | None = None,
    meeting: Meeting | None = None,
    rules_fired: tuple[str, ...] = (),
    dropped: tuple[str, ...] = (),
) -> Decision:
    return Decision(
        label=label,
        urgent=urgent,
        finance=finance,
        meeting=meeting,
        reply=reply,
        urgent_draft=urgent_draft,
        summary=SUMMARY,
        suspected_injection=None,
        decided_by=DecidedBy.MODEL,
        rules_fired=rules_fired,
        dropped=dropped,
        urgent_quote=urgent_quote,
    )
