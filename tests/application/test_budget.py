from dataclasses import replace
from decimal import Decimal

from fakes.scripted_model import response_with_text

from aeca.application.budget import Budget, Spend
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
