from decimal import Decimal

import pytest

from aeca.adapters.pricing import cost_usd, worst_case_cost_usd
from aeca.application.ports import Usage
from aeca.domain.errors import AecaError


def usage(**tokens: int) -> Usage:
    return Usage(
        **{"input_tokens": 0, "output_tokens": 0, "cache_read": 0, "cache_write": 0, **tokens}
    )


def test_opus_5_5_prices_input_and_output() -> None:
    assert cost_usd("claude-opus-5-5", usage(input_tokens=1000, output_tokens=100)) == Decimal(
        "0.006"
    )


def test_cache_reads_and_writes_have_their_own_rates() -> None:
    cost = cost_usd("claude-opus-5-5", usage(cache_read=1_000_000, cache_write=1_000_000))
    assert cost == Decimal("0.20") + Decimal("5.00")


def test_a_fallback_model_bills_at_its_own_rates() -> None:
    assert cost_usd(
        "claude-opus-4-8", usage(input_tokens=1_000_000, output_tokens=1_000_000)
    ) == Decimal("30.00")


@pytest.mark.parametrize(
    ("model", "input_rate", "output_rate"),
    [("claude-sonnet-5-5", "2.00", "10.00"), ("claude-haiku-5-5", "0.10", "0.50")],
)
def test_the_comparison_models_are_priced(model: str, input_rate: str, output_rate: str) -> None:
    cost = cost_usd(model, usage(input_tokens=1_000_000, output_tokens=1_000_000))
    assert cost == Decimal(input_rate) + Decimal(output_rate)


def test_an_unknown_model_is_an_error_never_a_zero() -> None:
    with pytest.raises(AecaError):
        cost_usd("claude-mystery-1", usage(input_tokens=1))


@pytest.mark.parametrize(
    ("model", "cache_read", "cache_write"),
    [
        ("claude-opus-5-5", "0.20", "5.00"),
        ("claude-opus-4-8", "0.50", "6.25"),
        ("claude-sonnet-5-5", "0.20", "2.50"),
        ("claude-haiku-5-5", "0.01", "0.125"),
    ],
)
def test_each_models_cache_rates_are_its_own(model: str, cache_read: str, cache_write: str) -> None:
    assert cost_usd(model, usage(cache_read=1_000_000)) == Decimal(cache_read)
    assert cost_usd(model, usage(cache_write=1_000_000)) == Decimal(cache_write)


def test_the_worst_case_cost_is_the_dearest_model_so_an_unpriced_call_still_counts() -> None:
    assert worst_case_cost_usd(usage(output_tokens=1_000_000)) == Decimal("25.00")
