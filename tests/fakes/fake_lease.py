"""A lease kept in memory. It applies the real rule, and records what it was asked."""

from datetime import datetime

from aeca.domain.errors import LockLost
from aeca.domain.lease import (
    LeaseHolding,
    LeaseOutcome,
    LeaseRequest,
    LeaseResult,
    decide_acquisition,
)


class FakeLease:
    def __init__(self, holding: LeaseHolding | None = None) -> None:
        self.holding = holding
        self.requests: list[LeaseRequest] = []
        self.renewals: list[datetime] = []

    def acquire(self, request: LeaseRequest) -> LeaseResult:
        self.requests.append(request)
        outcome = decide_acquisition(self.holding, request)
        result = LeaseResult(outcome=outcome, holding=self.holding)
        if outcome is not LeaseOutcome.HELD_BY_ANOTHER:
            self.holding = LeaseHolding(request.copy_id, request.now)
        return result

    def still_held(self, copy_id: str) -> bool:
        return self.holding is not None and self.holding.copy_id == copy_id

    def renew(self, copy_id: str, now: datetime) -> None:
        if not self.still_held(copy_id):
            raise LockLost.for_a_lost_lock()
        self.holding = LeaseHolding(copy_id, now)
        self.renewals.append(now)
