"""What a model call costs. The daily cap is only as good as this table."""

from dataclasses import dataclass
from decimal import Decimal

from aeca.application.ports import Usage
from aeca.domain.errors import AecaError

TOKENS_PER_MILLION = Decimal(1_000_000)


@dataclass(frozen=True, slots=True)
class Rates:
    """US dollars per million tokens, the cache write at the 5-minute rate."""

    input: Decimal
    output: Decimal
    cache_read: Decimal
    cache_write: Decimal


# Haiku 5.5 charges more above 100K prompt tokens. A decision prompt is the system prompt and one
# email bounded to 8,000 characters, so that tier is never reached.
PRICES: dict[str, Rates] = {
    "claude-opus-5-5": Rates(
        input=Decimal("4.00"),
        output=Decimal("20.00"),
        cache_read=Decimal("0.20"),
        cache_write=Decimal("5.00"),
    ),
    "claude-opus-5": Rates(
        input=Decimal("5.00"),
        output=Decimal("25.00"),
        cache_read=Decimal("0.50"),
        cache_write=Decimal("6.25"),
    ),
    "claude-opus-4-8": Rates(
        input=Decimal("5.00"),
        output=Decimal("25.00"),
        cache_read=Decimal("0.50"),
        cache_write=Decimal("6.25"),
    ),
    "claude-sonnet-5-5": Rates(
        input=Decimal("2.00"),
        output=Decimal("10.00"),
        cache_read=Decimal("0.20"),
        cache_write=Decimal("2.50"),
    ),
    "claude-haiku-5-5": Rates(
        input=Decimal("0.10"),
        output=Decimal("0.50"),
        cache_read=Decimal("0.01"),
        cache_write=Decimal("0.125"),
    ),
}


def is_priced(model: str) -> bool:
    return model in PRICES


# An unknown model raises rather than costing zero, because a zero would let a day pass the cap.
def cost_usd(model: str, usage: Usage) -> Decimal:
    rates = PRICES.get(model)
    if rates is None:
        raise AecaError(
            f"No price is known for model {model!r}, so its cost cannot be counted",
            next_action=(
                f"Add its price to aeca/adapters/pricing.py, or use one of: {', '.join(PRICES)}"
            ),
        )
    return _cost(rates, usage)


# For a call already paid for at a model nobody priced: the cap must still see it, and the dearest
# known rate is the safe guess.
def worst_case_cost_usd(usage: Usage) -> Decimal:
    return max(_cost(rates, usage) for rates in PRICES.values())


def _cost(rates: Rates, usage: Usage) -> Decimal:
    return (
        usage.input_tokens * rates.input
        + usage.output_tokens * rates.output
        + usage.cache_read * rates.cache_read
        + usage.cache_write * rates.cache_write
    ) / TOKENS_PER_MILLION
