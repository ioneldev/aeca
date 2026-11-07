from dataclasses import dataclass
from enum import StrEnum

from aeca.domain.actions import Action
from aeca.domain.time_range import Interval


class ApprovalState(StrEnum):
    AWAITING = "awaiting"
    APPROVED = "approved"
    EXECUTED = "executed"
    FAILED = "failed"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"


@dataclass(frozen=True)
class Hold:
    thread_id: str
    approval_id: str
    interval: Interval


@dataclass(frozen=True)
class Approval:
    id: str
    source_message_id: str
    source_thread_id: str
    attempt: int
    actions: tuple[Action, ...]
    state: ApprovalState
    request_thread_id: str | None
    summary: str
    holds: tuple[Interval, ...]
