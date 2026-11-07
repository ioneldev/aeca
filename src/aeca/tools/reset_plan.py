"""What reset-test will do, and how it is shown: every item with what it is and the rule that
matched, the totals per rule, and a summary of what is left alone.

It shows ids, dates, addresses, subjects and folders, never a body or a Gmail snippet: this output
is pasted into the run record (case N2).
"""

import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass

from aeca.adapters.google_reset import CalendarEntry
from aeca.domain.labels import DRAFT, INBOX, PROCESSED, SENT, SPAM_SYSTEM_LABEL
from aeca.domain.rehearsal_mail import LeftoverRule, MailMatch
from aeca.domain.text import visible

SUBJECT_SHOWN = 80
WHITESPACE_RUN = re.compile(r"\s+")
NO_SENDER = "(no From)"
FOLDER_NAMES = ((INBOX, "Inbox"), (SENT, "Sent"))


@dataclass(frozen=True)
class PlannedEvent:
    entry: CalendarEntry
    rule: LeftoverRule


@dataclass(frozen=True)
class SenderTally:
    sender: str
    messages: int
    unprocessed_in_inbox: int
    # The From is a rehearsal address, yet no rule matched: a spoof, or mail to check by hand.
    rehearsal_address: bool


@dataclass(frozen=True)
class ResetPlan:
    trash_messages: tuple[MailMatch, ...]
    cancel_events: tuple[PlannedEvent, ...]
    # Drafts a rule matches. They are listed, never trashed: what trashing a draft does is
    # unverified (cases P10b).
    drafts_left_alone: tuple[MailMatch, ...]
    left_alone: tuple[SenderTally, ...]

    @property
    def is_empty(self) -> bool:
        return not self.trash_messages and not self.cancel_events


def plan_lines(plan: ResetPlan, *, applying: bool) -> list[str]:
    return [
        _headline(applying),
        *_body_lines(plan),
        *_drafts_lines(plan.drafts_left_alone),
        *_left_alone_lines(plan.left_alone),
    ]


def _headline(applying: bool) -> str:
    if applying:
        return "reset-test --yes: applying the plan below."
    return (
        "reset-test, dry run: nothing is changed. "
        "To apply this plan, run make reset-test ARGS=--yes."
    )


def _body_lines(plan: ResetPlan) -> list[str]:
    if plan.is_empty:
        return ["", "Nothing to reset: no rehearsal mail outside Trash, no live rehearsal event."]
    return [
        "",
        f"Mail to move to Trash ({len(plan.trash_messages)}):",
        *(_message_line(match) for match in plan.trash_messages),
        "",
        f"Events to cancel ({len(plan.cancel_events)}):",
        *(_event_line(planned) for planned in plan.cancel_events),
        "",
        _totals_line(plan),
    ]


def _drafts_lines(drafts: tuple[MailMatch, ...]) -> list[str]:
    if not drafts:
        return []
    return [
        "",
        f"Drafts a rule matches, left alone; check them by hand ({len(drafts)}):",
        *(_message_line(match) for match in drafts),
    ]


def _left_alone_lines(tallies: tuple[SenderTally, ...]) -> list[str]:
    return [
        "",
        "Left alone, outside Trash, by sender "
        f"(Inbox mail without {PROCESSED} is processed by the next copy's first poll):",
        *(_tally_line(tally) for tally in tallies),
    ]


def _message_line(match: MailMatch) -> str:
    facts = match.item.facts
    return "  ".join(
        [
            f"  {facts.message_id}",
            facts.received_at.strftime("%Y-%m-%d %H:%MZ"),
            _folder(facts.label_ids),
            match.rule.value,
            f"from {facts.sender or NO_SENDER}",
            f"to {_recipients_shown(match.item.recipients)}",
            _quoted(facts.subject),
        ]
    )


def _event_line(planned: PlannedEvent) -> str:
    entry = planned.entry
    return "  ".join(
        [f"  {entry.id}", entry.starts[:16], planned.rule.value, _quoted(entry.summary)]
    )


def _totals_line(plan: ResetPlan) -> str:
    counts = Counter(
        [match.rule for match in plan.trash_messages]
        + [planned.rule for planned in plan.cancel_events]
    )
    return "Totals: " + ", ".join(f"{rule.value} {counts[rule]}" for rule in LeftoverRule)


def _tally_line(tally: SenderTally) -> str:
    noun = "message" if tally.messages == 1 else "messages"
    line = f"  {tally.sender or NO_SENDER}: {tally.messages} {noun}"
    if tally.unprocessed_in_inbox:
        line += f", {tally.unprocessed_in_inbox} in the Inbox without {PROCESSED}"
    if tally.rehearsal_address:
        line += " (a rehearsal address that no rule matched: a spoof, or one to check by hand)"
    return line


def _folder(labels: Iterable[str]) -> str:
    present = set(labels)
    if SPAM_SYSTEM_LABEL in present:
        return "Spam"
    if DRAFT in present:
        return "Drafts"
    return "+".join(name for label, name in FOLDER_NAMES if label in present) or "Archived"


def _recipients_shown(recipients: frozenset[str] | None) -> str:
    if recipients is None:
        return "(unreadable)"
    return ",".join(sorted(recipients)) or "(nobody)"


def _quoted(text: str) -> str:
    one_line = WHITESPACE_RUN.sub(" ", visible(text)).strip()
    return f'"{one_line[:SUBJECT_SHOWN]}"'
