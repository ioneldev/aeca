"""The day's cost cap."""

from decimal import Decimal

from aeca.application.ports import PaidCall


class Budget:
    """Tells whether the day's spending has reached its limit.

    A call's price is known only after it is paid for, so the last call can overshoot the limit by
    its own cost: the limit stops what starts, not what the last call costs.
    """

    def __init__(self, limit_usd: Decimal) -> None:
        self._limit_usd = limit_usd
        self._spent_usd = Decimal(0)

    @property
    def spent_usd(self) -> Decimal:
        return self._spent_usd

    def charge(self, call: PaidCall) -> None:
        self._spent_usd += call.cost_usd

    def exhausted(self) -> bool:
        return self._spent_usd >= self._limit_usd
