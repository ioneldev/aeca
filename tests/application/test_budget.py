from decimal import Decimal

from fakes.scripted_model import response_with_text

from aeca.application.budget import Budget


def test_a_budget_with_room_is_not_exhausted() -> None:
    assert not Budget(limit_usd=Decimal("1")).exhausted()


def test_charges_add_up_until_the_limit_is_reached() -> None:
    budget = Budget(limit_usd=Decimal("0.02"))
    budget.charge(response_with_text("a"))
    assert not budget.exhausted()
    budget.charge(response_with_text("b"))
    assert budget.exhausted()
    assert budget.spent_usd == Decimal("0.02")
