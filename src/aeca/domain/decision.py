"""The decision: what the model returns, and what code makes of it once it is validated."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field

from aeca.domain.labels import Label, ReplyKind


class ModelWindow(BaseModel):
    start: str
    end: str | None = None
    quote: str


class ModelMeeting(BaseModel):
    title: str
    duration_minutes: int | None = None
    windows: list[ModelWindow] = Field(default_factory=list)


class ModelDecision(BaseModel):
    primary_label: str
    urgent: bool = False
    urgent_quote: str | None = None
    finance: bool = False
    meeting: ModelMeeting | None = None
    reply: ReplyKind = ReplyKind.NONE
    urgent_draft: str | None = None
    summary: str
    suspected_injection: str | None = None


class DecidedBy(StrEnum):
    RULE = "rule"
    MODEL = "model"
    FALLBACK = "fallback"


@dataclass(frozen=True, slots=True)
class Window:
    start: datetime
    end: datetime | None
    quote: str


@dataclass(frozen=True, slots=True)
class Meeting:
    title: str
    duration_minutes: int | None
    windows: tuple[Window, ...]


@dataclass(frozen=True, slots=True)
class Decision:
    label: Label
    urgent: bool
    finance: bool
    meeting: Meeting | None
    reply: ReplyKind
    urgent_draft: str | None
    summary: str
    suspected_injection: str | None
    decided_by: DecidedBy
    rules_fired: tuple[str, ...]
    dropped: tuple[str, ...]
    # The text that shows the email is urgent, kept for the manager's notice.
    urgent_quote: str | None = None

    @staticmethod
    def fallback(summary: str) -> "Decision":
        return Decision(
            label=Label.UNKNOWN,
            urgent=False,
            finance=False,
            meeting=None,
            reply=ReplyKind.NONE,
            urgent_draft=None,
            summary=summary,
            suspected_injection=None,
            decided_by=DecidedBy.FALLBACK,
            rules_fired=(),
            dropped=(),
        )
