"""The Anthropic Messages API behind the LLMClient port. With httpx2, the only code that touches
the SDK."""

import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, NamedTuple, Self

import anthropic
import httpx2
from anthropic.types.beta import BetaMessage
from pydantic import BaseModel, ValidationError

from aeca.adapters.pricing import cost_usd
from aeca.application.ports import (
    LLMClient,
    LLMRequest,
    LLMResponse,
    PaidCall,
    ProviderError,
    ReplyUnusable,
    Tracer,
    Usage,
)
from aeca.domain.secret import SecretStr

MODELS_FILE = "aeca/config/models.yaml"
ATTEMPTS = 3
BACKOFF_SECONDS = (2, 4, 8)
FALLBACK_BETA = "server-side-fallback-2026-07-01"
PROMPT_CACHE = {"type": "ephemeral"}
VISIBLE_ASCII = re.compile(r"[\x21-\x7e]+")
PROBLEM_LIMIT = 300
RUN_AGAIN = "Wait a few minutes: AECA backs off and tries again by itself"


class _Failure(NamedTuple):
    sdk_error: type[anthropic.AnthropicError]
    kind: str
    problem: str
    next_action: str
    system_wide: bool
    # For a status the SDK has no class of its own for: it raises a plain APIStatusError.
    status_code: int | None = None
    # For a cause the API tells only in its message, under a status it shares with other causes.
    message_holds: str | None = None


# Ordered most specific first: the first entry the error is an instance of wins, so a timeout
# must come before the connection error it extends, and the base class comes last.
_FAILURES = (
    _Failure(
        sdk_error=anthropic.AuthenticationError,
        kind="authentication",
        problem="Anthropic rejected the API key",
        next_action="Set ANTHROPIC_API_KEY to a valid key, then restart AECA",
        system_wide=True,
    ),
    _Failure(
        sdk_error=anthropic.PermissionDeniedError,
        kind="permission",
        problem="This API key is not allowed to make this call",
        next_action=f"Use a key that may call the model, or fix the model in {MODELS_FILE}",
        system_wide=True,
    ),
    _Failure(
        sdk_error=anthropic.NotFoundError,
        kind="model_not_found",
        problem="Anthropic does not know this model",
        next_action=f"Fix the model name in {MODELS_FILE}, then restart AECA",
        system_wide=True,
    ),
    _Failure(
        sdk_error=anthropic.RateLimitError,
        kind="rate_limited",
        problem="Anthropic is rate limiting this key, and the retries did not clear it",
        next_action=RUN_AGAIN,
        system_wide=True,
    ),
    _Failure(
        sdk_error=anthropic.APIStatusError,
        status_code=402,
        kind="billing",
        problem="Anthropic reports a billing problem on this account",
        next_action="Fix the billing in the Anthropic Console",
        system_wide=True,
    ),
    _Failure(
        sdk_error=anthropic.BadRequestError,
        message_holds="usage limits",
        kind="usage_limit",
        problem="The Anthropic account has reached its usage limit",
        next_action="Raise the limit in the Anthropic Console, or wait until the date it names",
        system_wide=True,
    ),
    _Failure(
        sdk_error=anthropic.ContentTooLargeError,
        kind="too_large",
        problem="The request is too large for Anthropic",
        next_action="Report this email: its text is bounded, so a request this large is a bug",
        system_wide=False,
    ),
    _Failure(
        sdk_error=anthropic.BadRequestError,
        kind="bad_request",
        problem="Anthropic found the request invalid",
        next_action=f"Check the model settings in {MODELS_FILE}. If they are right, report this",
        system_wide=False,
    ),
    _Failure(
        sdk_error=anthropic.OverloadedError,
        kind="overloaded",
        problem="Anthropic is overloaded, and the retries did not clear it",
        next_action=RUN_AGAIN,
        system_wide=True,
    ),
    _Failure(
        sdk_error=anthropic.InternalServerError,
        kind="server_error",
        problem="Anthropic had a server error, and the retries did not clear it",
        next_action=RUN_AGAIN,
        system_wide=True,
    ),
    _Failure(
        sdk_error=anthropic.APITimeoutError,
        kind="timeout",
        problem="The request to Anthropic timed out, and the retries did not clear it",
        next_action="Check the network connection: AECA backs off and tries again by itself",
        system_wide=True,
    ),
    _Failure(
        sdk_error=anthropic.APIConnectionError,
        kind="connection",
        problem="Cannot reach Anthropic, and the retries did not clear it",
        next_action="Check the network connection: AECA backs off and tries again by itself",
        system_wide=True,
    ),
    _Failure(
        sdk_error=anthropic.AnthropicError,
        kind="unknown",
        problem="The Anthropic call failed",
        next_action="Report the error with its request id",
        system_wide=True,
    ),
)

_RETRYABLE_KINDS = frozenset(
    {"rate_limited", "overloaded", "server_error", "timeout", "connection"}
)


@dataclass(frozen=True, slots=True)
class _Classified:
    failure: _Failure
    status_code: int | None
    request_id: str | None

    @property
    def retryable(self) -> bool:
        return self.failure.kind in _RETRYABLE_KINDS or (
            self.failure.kind == "unknown"
            and self.status_code is not None
            and self.status_code >= 500
        )

    def as_provider_error(self) -> ProviderError:
        where = (
            f" (HTTP {self.status_code}, request id {self.request_id})" if self.status_code else ""
        )
        return ProviderError(
            f"{self.failure.problem}{where}",
            next_action=self.failure.next_action,
            kind=self.failure.kind,
            system_wide=self.failure.system_wide,
        )


class AnthropicClient(LLMClient):
    def __init__(
        self,
        sdk: anthropic.Anthropic,
        tracer: Tracer,
        *,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.perf_counter,
    ) -> None:
        self._sdk = sdk
        self._tracer = tracer
        self._sleep = sleep
        self._clock = clock

    # The SDK's own retries stay off, so that no retry goes unseen: this class retries and traces
    # each attempt. A SecretStr, and never a plain local, because a traceback prints the arguments
    # of every frame it passes through and --showlocals prints the locals.
    @classmethod
    def from_api_key(
        cls,
        api_key: SecretStr,
        tracer: Tracer,
        *,
        http_client: httpx2.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> Self:
        if VISIBLE_ASCII.fullmatch(api_key.reveal()) is None:
            raise _malformed_key()
        sdk = anthropic.Anthropic(api_key=api_key.reveal(), max_retries=0, http_client=http_client)
        return cls(sdk, tracer, sleep=sleep)

    # Validated here rather than by the SDK's parse(), so the usage still exists when the reply
    # does not fit: the caller has paid for it either way.
    def parse[T: BaseModel](self, request: LLMRequest, schema: type[T]) -> tuple[T, LLMResponse]:
        started = self._clock()
        reply = self._send_with_retries(_params(request, schema))
        paid = _paid_call(reply, latency_s=self._clock() - started)
        if reply.stop_reason == "refusal":
            raise _refused(paid)
        response = _response(reply, paid)
        return _read_reply(reply, schema, response), response

    def _send_with_retries(self, params: dict[str, Any]) -> BetaMessage:
        for attempt in range(1, ATTEMPTS + 1):
            try:
                reply: BetaMessage = self._sdk.beta.messages.create(**params)
            except anthropic.AnthropicError as error:
                classified = _classify(error)
            else:
                if attempt > 1:
                    self._tracer.event("model_retry", attempt=attempt, outcome="ok")
                return reply
            self._after_failed_attempt(attempt, classified)
        raise AssertionError("unreachable: the last failed attempt raises")

    # Raised after the handler, not inside it. Inside, Python links the SDK error as __context__
    # even for "from None", and a transport error's chain holds the request headers, the API key
    # among them.
    def _after_failed_attempt(self, attempt: int, classified: _Classified) -> None:
        last = attempt == ATTEMPTS
        retrying = classified.retryable and not last
        wait_s = BACKOFF_SECONDS[attempt - 1] if retrying else None
        self._tracer.event(
            "model_retry",
            attempt=attempt,
            outcome="retrying" if retrying else "failed",
            error_kind=classified.failure.kind,
            status=classified.status_code,
            wait_s=wait_s,
        )
        if wait_s is None:
            raise classified.as_provider_error() from None
        self._sleep(wait_s)


def _classify(error: anthropic.AnthropicError) -> _Classified:
    failure = next(failure for failure in _FAILURES if _applies_to(failure, error))
    status_code = error.status_code if isinstance(error, anthropic.APIStatusError) else None
    request_id = error.request_id if isinstance(error, anthropic.APIStatusError) else None
    return _Classified(failure, status_code, request_id)


def _applies_to(failure: _Failure, error: anthropic.AnthropicError) -> bool:
    if not isinstance(error, failure.sdk_error):
        return False
    if failure.message_holds is not None and failure.message_holds not in _api_message(error):
        return False
    return failure.status_code is None or getattr(error, "status_code", None) == failure.status_code


def _api_message(error: anthropic.AnthropicError) -> str:
    if not isinstance(error, anthropic.APIStatusError):
        return str(error).lower()
    body = error.body.get("error") if isinstance(error.body, dict) else None
    message = body.get("message") if isinstance(body, dict) else None
    return (message if isinstance(message, str) else error.message).lower()


def _malformed_key() -> ProviderError:
    return ProviderError(
        "The API key is empty, or holds a space, a line break or another character a key "
        "cannot have",
        next_action="Set ANTHROPIC_API_KEY to the key alone, with nothing before or after it",
        kind="malformed_key",
        system_wide=True,
    )


def _params(request: LLMRequest, schema: type[BaseModel]) -> dict[str, Any]:
    return {
        "model": request.model,
        "max_tokens": request.max_tokens,
        "system": [{"type": "text", "text": request.system, "cache_control": PROMPT_CACHE}],
        "messages": list(request.messages),
        "output_config": {
            "effort": request.effort,
            "format": {"type": "json_schema", "schema": anthropic.transform_schema(schema)},
        },
        "betas": [FALLBACK_BETA],
        "fallbacks": "default",
    }


def _paid_call(reply: BetaMessage, *, latency_s: float) -> PaidCall:
    usage = Usage(
        input_tokens=reply.usage.input_tokens,
        output_tokens=reply.usage.output_tokens,
        cache_read=reply.usage.cache_read_input_tokens or 0,
        cache_write=reply.usage.cache_creation_input_tokens or 0,
    )
    return PaidCall(
        usage=usage,
        model=str(reply.model),
        cost_usd=cost_usd(str(reply.model), usage),
        latency_s=latency_s,
    )


def _response(reply: BetaMessage, paid: PaidCall) -> LLMResponse:
    return LLMResponse(
        usage=paid.usage,
        model=paid.model,
        cost_usd=paid.cost_usd,
        latency_s=paid.latency_s,
        content=tuple(reply.content),
        request_id=getattr(reply, "_request_id", None),
    )


def _refused(paid: PaidCall) -> ProviderError:
    return ProviderError(
        "The model declined to answer, and so did every model it falls back to",
        next_action="Nothing to do: the email gets the UNKNOWN label",
        kind="refused",
        system_wide=False,
        paid_call=paid,
    )


def _read_reply[T: BaseModel](reply: BetaMessage, schema: type[T], response: LLMResponse) -> T:
    if reply.stop_reason != "end_turn":
        raise ReplyUnusable(
            f"the reply was cut off before it ended (stop reason {reply.stop_reason})",
            response=response,
        )
    text = "".join(block.text for block in reply.content if block.type == "text")
    try:
        return schema.model_validate_json(text)
    except ValidationError as error:
        raise ReplyUnusable(_describe(error), response=response) from None


# Names where the reply is wrong and why, and never what it held: the problem goes back to the
# model and into the trace.
def _describe(error: ValidationError) -> str:
    problems = (
        f"{'.'.join(str(part) for part in problem['loc']) or 'the reply'}: {problem['msg']}"
        for problem in error.errors(include_input=False, include_url=False)
    )
    return "; ".join(problems)[:PROBLEM_LIMIT]
