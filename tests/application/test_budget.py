from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from fakes.fixed_clock import FIXED_NOW, FixedClock
from fakes.scripted_model import response_with_text

from aeca.adapters.sqlite_state import SqliteState
from aeca.application.budget import Budget, Spend, StoreLedger
from aeca.application.ports import Usage


def test_a_budget_with_room_is_not_exhausted() -> None:
    assert not Budget(limit_usd=Decimal("1")).exhausted()


def test_charges_add_up_until_the_limit_is_reached() -> None:
    budget = Budget(limit_usd=Decimal("0.02"))
    budget.charge(response_with_text("a"))
    assert not budget.exhausted()
    budget.charge(response_with_text("b"))
    assert budget.exhausted()
    assert budget.spent_usd == Decimal("0.02")


def test_the_budget_totals_the_tokens_and_dollars_it_was_charged() -> None:
    budget = Budget(limit_usd=Decimal("1"))
    budget.charge(response_with_text("a"))
    budget.charge(response_with_text("b"))

    assert (budget.spent.tokens_in, budget.spent.tokens_out, budget.spent.usd) == (
        2000,
        200,
        Decimal("0.02"),
    )


def test_cached_input_counts_as_tokens_in() -> None:
    budget = Budget(limit_usd=Decimal("1"))
    response = response_with_text("a")
    cached = replace(
        response, usage=Usage(input_tokens=10, output_tokens=5, cache_read=7, cache_write=3)
    )

    budget.charge(cached)

    assert (budget.spent.tokens_in, budget.spent.tokens_out) == (20, 5)


def test_spend_since_a_snapshot_is_the_difference() -> None:
    budget = Budget(limit_usd=Decimal("1"))
    budget.charge(response_with_text("a"))
    before = budget.spent

    budget.charge(response_with_text("b"))

    assert budget.spent - before == Spend(tokens_in=1000, tokens_out=100, usd=Decimal("0.01"))


def test_a_budget_on_the_store_keeps_the_days_spend_across_a_restart(tmp_path: Path) -> None:
    clock = FixedClock()
    first = Budget(Decimal("0.02"), ledger=StoreLedger(SqliteState(tmp_path / "state.db"), clock))
    first.charge(response_with_text("a"))
    first.charge(response_with_text("b"))

    restarted = Budget(
        Decimal("0.02"), ledger=StoreLedger(SqliteState(tmp_path / "state.db"), clock)
    )

    assert restarted.exhausted()
    assert restarted.spent_usd == Decimal("0.02")


def test_a_new_utc_day_starts_with_nothing_spent(tmp_path: Path) -> None:
    store = SqliteState(tmp_path / "state.db")
    today = Budget(Decimal("0.01"), ledger=StoreLedger(store, FixedClock()))
    today.charge(response_with_text("a"))

    tomorrow = Budget(
        Decimal("0.01"), ledger=StoreLedger(store, FixedClock(FIXED_NOW + timedelta(days=1)))
    )

    assert today.exhausted()
    assert not tomorrow.exhausted()


def test_the_day_is_the_utc_day_whatever_the_zone_of_the_clock(tmp_path: Path) -> None:
    store = SqliteState(tmp_path / "state.db")
    late_in_utc = datetime(2026, 11, 7, 23, 59, tzinfo=UTC)
    Budget(Decimal(1), ledger=StoreLedger(store, FixedClock(late_in_utc))).charge(
        response_with_text("a")
    )

    assert store.spend_on(date(2026, 11, 7)) == Decimal("0.01")
    assert store.spend_on(date(2026, 11, 8)) == Decimal(0)


def test_the_per_email_difference_still_reads_the_tokens_on_a_store_budget(
    tmp_path: Path,
) -> None:
    budget = Budget(
        Decimal(1), ledger=StoreLedger(SqliteState(tmp_path / "state.db"), FixedClock())
    )
    budget.charge(response_with_text("a"))
    before = budget.spent

    budget.charge(response_with_text("b"))

    assert budget.spent - before == Spend(tokens_in=1000, tokens_out=100, usd=Decimal("0.01"))
