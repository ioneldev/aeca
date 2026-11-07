import json
from decimal import Decimal
from typing import Any

import anthropic
import httpx2
import pytest
from fakes.recording_tracer import RecordingTracer
from pydantic import BaseModel

from aeca.adapters.anthropic_client import AnthropicClient
from aeca.application.ports import LLMRequest, ProviderError, ReplyUnusable
from aeca.domain.decision import ModelDecision
from aeca.domain.secret import SecretStr

FAKE_KEY = SecretStr("sk-ant-fake-0123456789-not-a-real-key")
DECISION_JSON = json.dumps({"primary_label": "TASK", "summary": "Review the contract"})


def message_body(
    text: str = DECISION_JSON,
    *,
    stop_reason: str = "end_turn",
    model: str = "claude-opus-5-5",
    content: list[dict[str, Any]] | None = None,
    usage: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "id": "msg_1",
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": content if content is not None else [{"type": "text", "text": text}],
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": usage or {"input_tokens": 1000, "output_tokens": 100},
    }


def error_body(kind: str) -> dict[str, Any]:
    return {"type": "error", "error": {"type": kind, "message": f"{kind} happened"}}


class Server:
    """Answers each request from a list, and keeps the requests it saw."""

    def __init__(self, *answers: httpx2.Response | Exception) -> None:
        self.answers = list(answers)
        self.requests: list[httpx2.Request] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    def body(self, index: int = 0) -> dict[str, Any]:
        return json.loads(self.requests[index].content)


def ok(**kwargs: Any) -> httpx2.Response:
    return httpx2.Response(200, json=message_body(**kwargs))


def failing(status: int, kind: str = "api_error") -> httpx2.Response:
    return httpx2.Response(status, json=error_body(kind))


class Harness:
    def __init__(self, *answers: httpx2.Response | Exception) -> None:
        self.server = Server(*answers)
        self.tracer = RecordingTracer()
        self.sleeps: list[float] = []
        self.client = AnthropicClient.from_api_key(
            FAKE_KEY,
            self.tracer,
            http_client=anthropic.DefaultHttpxClient(transport=httpx2.MockTransport(self.server)),
            sleep=self.sleeps.append,
        )

    def parse[T: BaseModel](self, schema: type[T] = ModelDecision, **request: Any) -> Any:  # type: ignore[assignment]
        return self.client.parse(make_request(**request), schema)


def make_request(**overrides: Any) -> LLMRequest:
    fields: dict[str, Any] = {
        "model": "claude-opus-5-5",
        "effort": "low",
        "system": "You label emails.",
        "messages": ({"role": "user", "content": "the email"},),
        **overrides,
    }
    return LLMRequest(**fields)


def test_sdk_retries_are_off() -> None:
    assert Harness().client._sdk.max_retries == 0


def test_a_429_is_retried_and_each_attempt_traced() -> None:
    harness = Harness(failing(429, "rate_limit_error"), failing(429, "rate_limit_error"), ok())
    decision, response = harness.parse()
    assert decision.primary_label == "TASK"
    assert len(harness.server.requests) == 3
    assert len(harness.tracer.of_kind("model_retry")) == 3
    assert harness.sleeps == [2, 4]
    assert response.model == "claude-opus-5-5"


def test_a_5xx_and_a_529_are_retried() -> None:
    harness = Harness(failing(500), failing(529, "overloaded_error"), ok())
    harness.parse()
    assert harness.sleeps == [2, 4]


def test_a_dropped_connection_is_retried() -> None:
    harness = Harness(httpx2.ConnectError("boom"), ok())
    harness.parse()
    assert len(harness.server.requests) == 2


def test_three_failed_attempts_end_as_a_system_wide_error() -> None:
    harness = Harness(failing(429), failing(429), failing(429))
    with pytest.raises(ProviderError) as raised:
        harness.parse()
    assert raised.value.system_wide is True
    assert raised.value.kind == "rate_limited"
    assert len(harness.server.requests) == 3
    assert harness.sleeps == [2, 4]


@pytest.mark.parametrize(
    ("status", "kind", "system_wide"),
    [
        (401, "authentication_error", True),
        (403, "permission_error", True),
        (404, "not_found_error", True),
        (400, "invalid_request_error", False),
    ],
)
def test_client_errors_are_never_retried(status: int, kind: str, system_wide: bool) -> None:
    harness = Harness(failing(status, kind))
    with pytest.raises(ProviderError) as raised:
        harness.parse()
    assert raised.value.system_wide is system_wide
    assert len(harness.server.requests) == 1
    assert harness.sleeps == []
    assert raised.value.next_action


def test_the_request_asks_for_structured_output_cached_system_and_fallbacks() -> None:
    harness = Harness(ok())
    harness.parse()
    request = harness.server.requests[0]
    body = harness.server.body()
    assert request.url.path == "/v1/messages"
    assert "server-side-fallback-2026-07-01" in request.headers["anthropic-beta"]
    assert body["fallbacks"] == "default"
    assert body["model"] == "claude-opus-5-5"
    assert body["max_tokens"] == 16000
    assert body["system"] == [
        {"type": "text", "text": "You label emails.", "cache_control": {"type": "ephemeral"}}
    ]
    assert body["output_config"]["effort"] == "low"
    assert body["output_config"]["format"]["type"] == "json_schema"
    assert body["messages"] == [{"role": "user", "content": "the email"}]


@pytest.mark.parametrize("forbidden", ["temperature", "top_p", "top_k", "thinking", "tool_choice"])
def test_the_request_sends_no_parameter_opus_5_5_refuses(forbidden: str) -> None:
    harness = Harness(ok())
    harness.parse()
    assert forbidden not in harness.server.body()


def test_the_request_never_carries_the_api_key_in_the_trace() -> None:
    harness = Harness(failing(500), ok())
    harness.parse()
    assert FAKE_KEY.reveal() not in repr(harness.tracer.events)


def test_the_assistant_turn_is_replayed_thinking_blocks_included() -> None:
    thinking = {"type": "thinking", "thinking": "let me see", "signature": "sig-1"}
    harness = Harness(
        ok(
            content=[
                thinking,
                {"type": "text", "text": json.dumps({"primary_label": "X", "summary": "s"})},
            ]
        ),
        ok(),
    )
    _, first = harness.parse()
    harness.parse(
        messages=(
            {"role": "user", "content": "the email"},
            {"role": "assistant", "content": first.content},
            {"role": "user", "content": "fix it"},
        )
    )
    replayed = harness.server.body(1)["messages"][1]["content"]
    assert replayed[0]["type"] == "thinking"
    assert replayed[0]["signature"] == "sig-1"
    assert replayed[0]["thinking"] == "let me see"


def test_usage_and_cost_are_read_from_the_reply() -> None:
    usage = {
        "input_tokens": 1000,
        "output_tokens": 100,
        "cache_read_input_tokens": 2000,
        "cache_creation_input_tokens": 500,
    }
    _, response = Harness(ok(usage=usage)).parse()
    assert (response.usage.input_tokens, response.usage.output_tokens) == (1000, 100)
    assert (response.usage.cache_read, response.usage.cache_write) == (2000, 500)
    # 1000*4 + 100*20 + 2000*0.2 + 500*5 per million
    assert response.cost_usd == Decimal("0.0089")


def test_a_fallback_model_is_priced_by_the_model_that_served_it() -> None:
    _, response = Harness(ok(model="claude-opus-4-8")).parse()
    assert response.model == "claude-opus-4-8"
    assert float(response.cost_usd) == pytest.approx(1000 * 5e-6 + 100 * 25e-6)


def test_a_refusal_raises_a_charged_refused_error_that_is_not_system_wide() -> None:
    harness = Harness(ok(text="", content=[], stop_reason="refusal"))
    with pytest.raises(ProviderError) as raised:
        harness.parse()
    assert raised.value.kind == "refused"
    assert raised.value.system_wide is False
    assert raised.value.paid_call is not None
    assert raised.value.paid_call.usage.input_tokens == 1000


def test_a_reply_cut_off_at_max_tokens_is_unusable_but_keeps_its_usage() -> None:
    harness = Harness(ok(text='{"primary_', stop_reason="max_tokens"))
    with pytest.raises(ReplyUnusable) as raised:
        harness.parse()
    assert "cut off" in raised.value.problem
    assert raised.value.response.usage.output_tokens == 100


def test_a_reply_that_is_not_json_is_unusable() -> None:
    with pytest.raises(ReplyUnusable):
        Harness(ok(text="I think it is a task")).parse()


def test_a_reply_that_breaks_the_schema_names_the_field_and_not_the_value() -> None:
    wrong = json.dumps({"primary_label": "TASK", "summary": 7, "urgent": "SECRET-VALUE"})
    with pytest.raises(ReplyUnusable) as raised:
        Harness(ok(text=wrong)).parse()
    assert "summary" in raised.value.problem
    assert "SECRET-VALUE" not in raised.value.problem


@pytest.mark.parametrize("key", ["", "has space", "line\nbreak", "ünïcode"])
def test_a_malformed_key_is_refused_before_any_request(key: str) -> None:
    with pytest.raises(ProviderError) as raised:
        AnthropicClient.from_api_key(SecretStr(key), RecordingTracer())
    assert raised.value.system_wide is True
    assert key not in str(raised.value) or key == ""


def fallback_block(
    declined: str = "claude-opus-5-5", served: str = "claude-opus-4-8"
) -> dict[str, Any]:
    return {
        "type": "fallback",
        "from": {"model": declined},
        "to": {"model": served},
        "trigger": {"type": "refusal"},
    }


def test_a_402_billing_failure_is_system_wide_and_not_retried() -> None:
    harness = Harness(failing(402, "billing_error"))
    with pytest.raises(ProviderError) as raised:
        harness.parse()
    assert (raised.value.kind, raised.value.system_wide) == ("billing", True)
    assert len(harness.server.requests) == 1


def test_a_400_saying_the_credit_balance_is_too_low_is_system_wide() -> None:
    body = {
        "type": "error",
        "error": {
            "type": "invalid_request_error",
            "message": "Your credit balance is too low to access the API.",
        },
    }
    harness = Harness(httpx2.Response(400, json=body))
    with pytest.raises(ProviderError) as raised:
        harness.parse()
    assert (raised.value.kind, raised.value.system_wide) == ("billing", True)
    assert len(harness.server.requests) == 1


def test_a_408_is_retried() -> None:
    harness = Harness(failing(408, "timeout_error"), ok())
    harness.parse()
    assert harness.sleeps == [2]


def test_the_decision_is_read_from_the_text_after_the_last_fallback_block() -> None:
    content = [
        {"type": "text", "text": "I cannot help with"},
        fallback_block(),
        {"type": "text", "text": DECISION_JSON},
    ]
    decision, _ = Harness(ok(content=content, model="claude-opus-4-8")).parse()
    assert decision.primary_label == "TASK"


def test_with_several_text_blocks_and_no_fallback_the_last_one_is_the_decision() -> None:
    content = [{"type": "text", "text": "preamble"}, {"type": "text", "text": DECISION_JSON}]
    decision, _ = Harness(ok(content=content)).parse()
    assert decision.primary_label == "TASK"


def test_each_iteration_of_a_fallback_served_call_is_priced_at_its_own_model() -> None:
    usage = {
        "input_tokens": 3000,
        "output_tokens": 300,
        "iterations": [
            {
                "type": "message",
                "model": "claude-opus-5-5",
                "input_tokens": 1000,
                "output_tokens": 100,
                "cache_read_input_tokens": 0,
                "cache_creation_input_tokens": 0,
            },
            {
                "type": "fallback_message",
                "model": "claude-opus-4-8",
                "input_tokens": 2000,
                "output_tokens": 200,
                "cache_read_input_tokens": 0,
                "cache_creation_input_tokens": 0,
            },
        ],
    }
    _, response = Harness(ok(usage=usage, model="claude-opus-4-8")).parse()
    expected = Decimal("1000") * Decimal("4") / 1_000_000 + Decimal("100") * 20 / 1_000_000
    expected += Decimal("2000") * 5 / 1_000_000 + Decimal("200") * 25 / 1_000_000
    assert response.cost_usd == expected
    assert response.model == "claude-opus-4-8"
    assert response.usage.input_tokens == 3000


def test_a_call_without_iterations_is_priced_at_the_reply_model() -> None:
    _, response = Harness(ok(model="claude-opus-4-8")).parse()
    assert float(response.cost_usd) == pytest.approx(1000 * 5e-6 + 100 * 25e-6)


def test_an_unpriced_served_model_is_charged_at_the_dearest_rate_traced_and_carried_on() -> None:
    harness = Harness(ok(model="claude-mystery-1"))
    decision, response = harness.parse()
    assert decision.primary_label == "TASK"
    assert response.model == "claude-mystery-1"
    assert response.cost_usd == Decimal(1000) * 5 / 1_000_000 + Decimal(100) * 25 / 1_000_000
    [event] = harness.tracer.of_kind("unpriced_model")
    assert event.fields["model"] == "claude-mystery-1"


def test_a_priced_model_emits_no_unpriced_event() -> None:
    harness = Harness(ok())
    harness.parse()
    assert harness.tracer.of_kind("unpriced_model") == []


MODEL_RECORD = {
    "id": "claude-opus-5-5",
    "type": "model",
    "display_name": "Claude Opus 5.5",
    "created_at": "2026-01-01T00:00:00Z",
}


def test_check_access_asks_for_the_model_record_which_costs_nothing() -> None:
    harness = Harness(httpx2.Response(200, json=MODEL_RECORD))

    harness.client.check_access("claude-opus-5-5")

    assert harness.server.requests[0].method == "GET"
    assert harness.server.requests[0].url.path.endswith("/models/claude-opus-5-5")


@pytest.mark.parametrize("status", [401, 403])
def test_check_access_reports_a_refused_key_as_key_refused(status: int) -> None:
    kind = "authentication_error" if status == 401 else "permission_error"
    harness = Harness(failing(status, kind))

    with pytest.raises(ProviderError) as raised:
        harness.client.check_access("claude-opus-5-5")

    assert (raised.value.kind, raised.value.system_wide) == ("key_refused", True)
    assert "key" in raised.value.next_action.lower()
    assert harness.tracer.of_kind("model_retry")


def test_check_access_names_a_model_the_api_does_not_know() -> None:
    harness = Harness(failing(404, "not_found_error"))

    with pytest.raises(ProviderError) as raised:
        harness.client.check_access("claude-nope")

    assert raised.value.kind == "model_not_found"
