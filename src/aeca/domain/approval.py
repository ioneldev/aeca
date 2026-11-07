from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum

from aeca.domain.actions import Action
from aeca.domain.errors import AecaError
from aeca.domain.time_range import Interval


class ApprovalState(StrEnum):
    AWAITING = "awaiting"
    APPROVED = "approved"
    EXECUTED = "executed"
    FAILED = "failed"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"


# Spec §4.5: awaiting → approved → executed | failed; awaiting → rejected | superseded.
_NEXT_STATES: Mapping[ApprovalState, frozenset[ApprovalState]] = {
    ApprovalState.AWAITING: frozenset(
        {ApprovalState.APPROVED, ApprovalState.REJECTED, ApprovalState.SUPERSEDED}
    ),
    ApprovalState.APPROVED: frozenset({ApprovalState.EXECUTED, ApprovalState.FAILED}),
}
OPEN_STATES = frozenset({ApprovalState.AWAITING, ApprovalState.APPROVED})


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
    # For `aeca approvals`. An approval saved before they were kept has neither.
    opened_at: datetime | None = None
    source_subject: str = ""

    @property
    def is_open(self) -> bool:
        return self.state in OPEN_STATES

    def approve(self) -> "Approval":
        return self._moved_to(ApprovalState.APPROVED)

    def reject(self) -> "Approval":
        return self._moved_to(ApprovalState.REJECTED)

    def supersede(self) -> "Approval":
        return self._moved_to(ApprovalState.SUPERSEDED)

    def executed(self) -> "Approval":
        return self._moved_to(ApprovalState.EXECUTED)

    def failed(self) -> "Approval":
        return self._moved_to(ApprovalState.FAILED)

    def _moved_to(self, state: ApprovalState) -> "Approval":
        if state not in _NEXT_STATES.get(self.state, frozenset()):
            raise AecaError(
                f"approval {self.id} cannot move from {self.state.value} to {state.value}",
                next_action="Fix the caller: only awaiting and approved approvals change state",
            )
        return replace(self, state=state)
