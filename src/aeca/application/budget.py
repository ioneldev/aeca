"""The day's cost cap."""

from dataclasses import dataclass
from decimal import Decimal

from aeca.application.ports import PaidCall


@dataclass(frozen=True, slots=True)
class Spend:
    tokens_in: int
    tokens_out: int
    usd: Decimal

    def __sub__(self, earlier: "Spend") -> "Spend":
        return Spend(
            tokens_in=self.tokens_in - earlier.tokens_in,
            tokens_out=self.tokens_out - earlier.tokens_out,
            usd=self.usd - earlier.usd,
        )


NO_SPEND = Spend(tokens_in=0, tokens_out=0, usd=Decimal(0))


class Budget:
    """Tells whether the day's spending has reached its limit.

    A call's price is known only after it is paid for, so the last call can overshoot the limit by
    its own cost: the limit stops what starts, not what the last call costs.
    """

    def __init__(self, limit_usd: Decimal) -> None:
        self._limit_usd = limit_usd
        self._spent = NO_SPEND

    @property
    def spent(self) -> Spend:
        return self._spent

    @property
    def spent_usd(self) -> Decimal:
        return self._spent.usd

    # Cached input is input too: it is the tokens the model read for this email.
    def charge(self, call: PaidCall) -> None:
        usage = call.usage
        self._spent = Spend(
            tokens_in=self._spent.tokens_in
            + usage.input_tokens
            + usage.cache_read
            + usage.cache_write,
            tokens_out=self._spent.tokens_out + usage.output_tokens,
            usd=self._spent.usd + call.cost_usd,
        )

    def exhausted(self) -> bool:
        return self._spent.usd >= self._limit_usd
