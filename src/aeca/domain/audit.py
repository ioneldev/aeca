from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from aeca.domain.actions import Action, ActionStatus

ActionSummary = tuple[str, str, str]


class Outcome(StrEnum):
    DONE = "done"
    AWAITING_APPROVAL = "awaiting_approval"
    FAILED = "failed"
    # A reply to an approval request that does not count, or came after the approval closed.
    IGNORED = "ignored"


def action_summary(action: Action, status: ActionStatus) -> ActionSummary:
    detail = action.args.get("label") or action.args.get("template") or ""
    return (action.verb.value, detail, status.value)


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
