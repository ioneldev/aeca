"""Checks the model's decision field by field.

A bad label is the only fault that refuses the whole decision. Any other fault drops the field it
is in, keeps the label, and is noted in `Decision.dropped`, so one bad field never turns a
correctly labelled email into UNKNOWN.
"""

import re
from dataclasses import dataclass, field
from datetime import datetime
from zoneinfo import ZoneInfo

from aeca.domain.decision import (
    DecidedBy,
    Decision,
    Meeting,
    ModelDecision,
    ModelMeeting,
    ModelWindow,
    Window,
)
from aeca.domain.facts import EmailFacts
from aeca.domain.labels import LABEL_RECORDS, Label, ReplyKind
from aeca.domain.replies import check_urgent_draft
from aeca.domain.text import bounded, visible

SUMMARY_LIMIT = 200
SUSPECTED_INJECTION_LIMIT = 500
TITLE_LIMIT = 200
QUOTE_LIMIT = 500
MIN_MEETING_MINUTES = 5
MAX_MEETING_MINUTES = 480

WHITESPACE_RUN = re.compile(r"\s+")
QUOTE_MARKS = str.maketrans(
    {"\u201c": '"', "\u201d": '"', "\u201e": '"', "\u2018": "'", "\u2019": "'"}
)


@dataclass(frozen=True, slots=True)
class ValidationContext:
    facts: EmailFacts
    now: datetime
    zone: ZoneInfo


@dataclass(frozen=True, slots=True)
class LabelRefused:
    reason: str


def validate(submitted: ModelDecision, context: ValidationContext) -> Decision | LabelRefused:
    label = _read_label(submitted.primary_label)
    if label is None:
        return LabelRefused(reason=_why_refused(submitted.primary_label))
    return _FieldValidation(submitted, label, context).decision()


def _read_label(submitted: str) -> Label | None:
    wanted = submitted.strip().casefold()
    return next((label for label in Label if label.value.casefold() == wanted), None)


def _why_refused(submitted: str) -> str:
    if not submitted.strip():
        return "the primary label is missing"
    return f"{submitted.strip()} is not a label"


@dataclass(slots=True)
class _FieldValidation:
    submitted: ModelDecision
    label: Label
    context: ValidationContext
    dropped: list[str] = field(default_factory=list)

    def decision(self) -> Decision:
        urgent = self._urgent()
        return Decision(
            label=self.label,
            urgent=urgent,
            finance=self.submitted.finance,
            meeting=self._meeting(),
            reply=self._reply(),
            urgent_draft=self._urgent_draft(urgent=urgent),
            summary=bounded(visible(self.submitted.summary), SUMMARY_LIMIT),
            suspected_injection=self._suspected_injection(),
            decided_by=DecidedBy.MODEL,
            rules_fired=(),
            dropped=tuple(self.dropped),
            urgent_quote=self._urgent_quote() if urgent else None,
        )

    def _urgent(self) -> bool:
        if not self.submitted.urgent:
            return False
        if self.label is Label.SPAM:
            self.dropped.append("urgent: never combined with SPAM")
            return False
        if not _quote_is_in_email(self.submitted.urgent_quote, self.context.facts):
            self.dropped.append("urgent: its quote is not in the email")
            return False
        return True

    def _urgent_quote(self) -> str:
        return bounded(visible((self.submitted.urgent_quote or "").strip()), QUOTE_LIMIT)

    def _reply(self) -> ReplyKind:
        reply = self.submitted.reply
        if reply is ReplyKind.NONE or reply in LABEL_RECORDS[self.label].allowed_replies:
            return reply
        self.dropped.append(f"reply: {reply.value} is not allowed on {self.label.value}")
        return ReplyKind.NONE

    def _urgent_draft(self, *, urgent: bool) -> str | None:
        draft = self.submitted.urgent_draft
        if draft is None or not draft.strip():
            return None
        if not (urgent and self.context.facts.internal):
            self.dropped.append("urgent_draft: only for an urgent email from an internal sender")
            return None
        broken_rule = check_urgent_draft(draft)
        if broken_rule is not None:
            self.dropped.append(f"urgent_draft: {broken_rule}")
            return None
        return visible(draft)

    def _suspected_injection(self) -> str | None:
        suspected = self.submitted.suspected_injection
        if suspected is None or not suspected.strip():
            return None
        return bounded(visible(suspected), SUSPECTED_INJECTION_LIMIT)

    def _meeting(self) -> Meeting | None:
        block = self.submitted.meeting
        if self.label is not Label.MEETING_REQUEST:
            if block is not None:
                self.dropped.append(f"meeting: not allowed on {self.label.value}")
            return None
        return Meeting(
            title=self._meeting_title(block),
            duration_minutes=self._duration(block),
            windows=self._windows(block),
        )

    def _meeting_title(self, block: ModelMeeting | None) -> str:
        title = block.title.strip() if block is not None else ""
        return bounded(visible(title or self.context.facts.subject), TITLE_LIMIT)

    def _duration(self, block: ModelMeeting | None) -> int | None:
        minutes = block.duration_minutes if block is not None else None
        if minutes is None or MIN_MEETING_MINUTES <= minutes <= MAX_MEETING_MINUTES:
            return minutes
        self.dropped.append(
            f"meeting.duration_minutes: outside {MIN_MEETING_MINUTES}-{MAX_MEETING_MINUTES}"
        )
        return None

    def _windows(self, block: ModelMeeting | None) -> tuple[Window, ...]:
        submitted = block.windows if block is not None else []
        checked = (self._window(position, window) for position, window in enumerate(submitted))
        return tuple(window for window in checked if window is not None)

    def _window(self, position: int, submitted: ModelWindow) -> Window | None:
        where = f"meeting.windows[{position}]"
        if not _quote_is_in_email(submitted.quote, self.context.facts):
            self.dropped.append(f"{where}: quote is not in the email")
            return None
        start = self._read_time(submitted.start)
        if start is None:
            self.dropped.append(f"{where}: start is not a time")
            return None
        if start < self.context.now:
            self.dropped.append(f"{where}: start is in the past")
            return None
        end = self._read_end(where, submitted.end)
        if end is not None and end <= start:
            self.dropped.append(f"{where}: end is not after start")
            return None
        return Window(start=start, end=end, quote=bounded(visible(submitted.quote), QUOTE_LIMIT))

    def _read_end(self, where: str, submitted: str | None) -> datetime | None:
        if submitted is None:
            return None
        end = self._read_time(submitted)
        if end is None:
            self.dropped.append(f"{where}: end is not a time")
        return end

    def _read_time(self, submitted: str) -> datetime | None:
        try:
            parsed = datetime.fromisoformat(submitted.strip())
        except ValueError:
            return None
        return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=self.context.zone)


def _quote_is_in_email(quote: str | None, facts: EmailFacts) -> bool:
    normalised_quote = _normalised(quote or "")
    if not normalised_quote:
        return False
    return normalised_quote in _normalised(f"{facts.subject}\n{facts.text}")


def _normalised(text: str) -> str:
    return WHITESPACE_RUN.sub(" ", text.translate(QUOTE_MARKS)).strip().lower()
