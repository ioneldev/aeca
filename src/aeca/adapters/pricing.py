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


def _rates(input: str, output: str, cache_read: str, cache_write: str) -> Rates:
    return Rates(Decimal(input), Decimal(output), Decimal(cache_read), Decimal(cache_write))


# Haiku 5.5 charges more above 100K prompt tokens. A decision prompt is the system prompt and one
# email bounded to 8,000 characters, so that tier is never reached.
PRICES: dict[str, Rates] = {
    "claude-opus-5-5": _rates("4.00", "20.00", "0.20", "5.00"),
    "claude-opus-4-8": _rates("5.00", "25.00", "0.50", "6.25"),
    "claude-sonnet-5-5": _rates("2.00", "10.00", "0.20", "2.50"),
    "claude-haiku-5-5": _rates("0.10", "0.50", "0.01", "0.125"),
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
    return (
        usage.input_tokens * rates.input
        + usage.output_tokens * rates.output
        + usage.cache_read * rates.cache_read
        + usage.cache_write * rates.cache_write
    ) / TOKENS_PER_MILLION
