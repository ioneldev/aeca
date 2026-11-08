"""The day's cost cap."""

from dataclasses import dataclass
from datetime import UTC, date
from decimal import Decimal
from typing import Protocol

from aeca.application.ports import Clock, PaidCall


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


class SpendLedger(Protocol):
    """Where the day's dollars are kept."""

    def add(self, usd: Decimal) -> None: ...

    def today(self) -> Decimal: ...


class SpendStore(Protocol):
    def spend_add(self, day: date, usd: Decimal) -> Decimal: ...

    def spend_on(self, day: date) -> Decimal: ...


class MemoryLedger:
    """For a tool that must not touch the service's state: it forgets with the process."""

    def __init__(self) -> None:
        self._total = Decimal(0)

    def add(self, usd: Decimal) -> None:
        self._total += usd

    def today(self) -> Decimal:
        return self._total


class StoreLedger:
    """The service's ledger: kept in the state file by UTC date, so a restart cannot reset the
    day's cap and midnight UTC starts a fresh day."""

    def __init__(self, store: SpendStore, clock: Clock) -> None:
        self._store = store
        self._clock = clock

    def add(self, usd: Decimal) -> None:
        self._store.spend_add(self._utc_day(), usd)

    def today(self) -> Decimal:
        return self._store.spend_on(self._utc_day())

    def _utc_day(self) -> date:
        return self._clock.now().astimezone(UTC).date()


class Budget:
    """Tells whether the day's spending has reached its limit.

    A call's price is known only after it is paid for, so the last call can overshoot the limit by
    its own cost: the limit stops what starts, not what the last call costs.
    """

    def __init__(self, limit_usd: Decimal, ledger: SpendLedger | None = None) -> None:
        self._limit_usd = limit_usd
        self._ledger = ledger or MemoryLedger()
        # This process's own totals, for the audit line's per-email difference. The day's dollars
        # live in the ledger.
        self._spent = NO_SPEND

    @property
    def spent(self) -> Spend:
        return self._spent

    @property
    def spent_usd(self) -> Decimal:
        return self._ledger.today()

    # Cached input is input too: it is the tokens the model read for this email. A paid call
    # followed by a system-wide failure is charged too: the money was spent, though no audit
    # line carries it.
    def charge(self, call: PaidCall) -> None:
        self._ledger.add(call.cost_usd)
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
        return self._ledger.today() >= self._limit_usd
