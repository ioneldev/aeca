import json
from dataclasses import dataclass
from datetime import datetime

from aeca.domain.actions import Action
from aeca.domain.decision import Decision
from aeca.domain.time_range import Interval


@dataclass(frozen=True)
class JournalEntry:
    source_message_id: str
    decision_json: str
    actions: tuple[Action, ...]
    # Kept in the journal so a resume can reopen the approval, and never registered as actions:
    # nothing may run them before the manager approves.
    awaiting_approval: tuple[Action, ...] = ()
    # The email's audit line is written: a resume must not write a second one.
    audited: bool = False


@dataclass(frozen=True)
class JournalledDecision:
    """The decision as the journal keeps it. A resume never asks the model again, so this holds
    everything the audit line and a reopened approval need, holds included."""

    label: str
    urgent: bool
    finance: bool
    reply: str
    summary: str
    decided_by: str
    rules_fired: tuple[str, ...]
    dropped: tuple[str, ...]
    holds: tuple[Interval, ...]
    # What the plan found that no rule decided, such as an invitation already on the calendar.
    notes: tuple[str, ...]

    @property
    def audit_rules(self) -> tuple[str, ...]:
        return self.rules_fired + self.dropped + self.notes

    @staticmethod
    def of(
        decision: Decision, *, holds: tuple[Interval, ...], notes: tuple[str, ...]
    ) -> "JournalledDecision":
        return JournalledDecision(
            label=decision.label.value,
            urgent=decision.urgent,
            finance=decision.finance,
            reply=decision.reply.value,
            summary=decision.summary,
            decided_by=decision.decided_by.value,
            rules_fired=decision.rules_fired,
            dropped=decision.dropped,
            holds=holds,
            notes=notes,
        )

    def to_json(self) -> str:
        return json.dumps(
            {
                "label": self.label,
                "urgent": self.urgent,
                "finance": self.finance,
                "reply": self.reply,
                "summary": self.summary,
                "decided_by": self.decided_by,
                "rules_fired": list(self.rules_fired),
                "dropped": list(self.dropped),
                "holds": [[hold.start.isoformat(), hold.end.isoformat()] for hold in self.holds],
                "notes": list(self.notes),
            }
        )

    @staticmethod
    def from_json(text: str) -> "JournalledDecision":
        fields = json.loads(text)
        return JournalledDecision(
            label=fields["label"],
            urgent=fields["urgent"],
            finance=fields["finance"],
            reply=fields["reply"],
            summary=fields["summary"],
            decided_by=fields["decided_by"],
            rules_fired=tuple(fields["rules_fired"]),
            dropped=tuple(fields["dropped"]),
            holds=tuple(_interval(pair) for pair in fields["holds"]),
            notes=tuple(fields["notes"]),
        )


def _interval(pair: list[str]) -> Interval:
    return Interval(start=datetime.fromisoformat(pair[0]), end=datetime.fromisoformat(pair[1]))
