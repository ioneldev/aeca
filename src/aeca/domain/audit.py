from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

ActionSummary = tuple[str, str, str]


class Outcome(StrEnum):
    DONE = "done"
    FAILED = "failed"


@dataclass(frozen=True)
class AuditLine:
    """One processed email or approval outcome. It has no field for the body, by design."""

    time: datetime
    message_id: str
    thread_id: str
    sender: str
    subject: str
    label: str
    urgent: bool
    decided_by: str
    rules: tuple[str, ...]
    actions: tuple[ActionSummary, ...]
    approval_id: str | None
    approval_state: str | None
    outcome: str
    error: str | None
    tokens_in: int
    tokens_out: int
    usd: Decimal
