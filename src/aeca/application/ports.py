"""What the application needs from the outside world. Adapters implement these."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Protocol

from pydantic import BaseModel

from aeca.domain.errors import AecaError

DEFAULT_MAX_TOKENS = 16000


class Clock(Protocol):
    def now(self) -> datetime:
        """The current time, aware and in UTC."""
        ...


class Tracer(Protocol):
    def event(self, kind: str, **fields: object) -> None: ...

    def debug(self, text: str) -> None: ...


@dataclass(frozen=True, slots=True)
class Usage:
    input_tokens: int
    output_tokens: int
    cache_read: int
    cache_write: int


@dataclass(frozen=True, slots=True)
class PaidCall:
    """What one call cost. A call that failed afterwards was still paid for."""

    usage: Usage
    model: str
    cost_usd: Decimal
    latency_s: float


@dataclass(frozen=True, slots=True)
class LLMResponse(PaidCall):
    # Sent back unchanged as the assistant turn of a correction, because the API checks thinking
    # blocks byte for byte.
    content: tuple[object, ...]
    request_id: str | None


@dataclass(frozen=True, slots=True)
class LLMRequest:
    model: str
    effort: str
    system: str
    messages: tuple[dict[str, object], ...]
    max_tokens: int = DEFAULT_MAX_TOKENS


class ProviderError(AecaError):
    """The model provider failed.

    system_wide says that every email would fail the same way, so the whole tick stops. paid_call
    holds what the API reported before the failure, for the caller to charge.
    """

    def __init__(
        self,
        message: str,
        *,
        next_action: str,
        kind: str,
        system_wide: bool,
        paid_call: PaidCall | None = None,
    ) -> None:
        super().__init__(message, next_action=next_action)
        self.kind = kind
        self.system_wide = system_wide
        self.paid_call = paid_call


class ReplyUnusable(AecaError):
    """The reply came back whole, and was paid for, but holds no decision that fits the schema."""

    def __init__(self, problem: str, *, response: LLMResponse) -> None:
        super().__init__(
            f"The model's reply is unusable: {problem}",
            next_action="Send the model a correction, or give the email the fallback decision",
        )
        self.problem = problem
        self.response = response


class LLMClient(Protocol):
    def parse[T: BaseModel](self, request: LLMRequest, schema: type[T]) -> tuple[T, LLMResponse]:
        """Raises ProviderError when the call fails or is refused, and ReplyUnusable when the
        reply does not fit the schema."""
        ...
