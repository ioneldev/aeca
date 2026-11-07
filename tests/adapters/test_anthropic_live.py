"""One real decision call. Costs money, so it only runs under make eval."""

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest
from fakes.recording_tracer import RecordingTracer
from support.facts import make_facts

from aeca.adapters.anthropic_client import AnthropicClient
from aeca.adapters.models_file import load_models
from aeca.application.decision_prompt import first_user_message, system_prompt
from aeca.application.ports import LLMRequest
from aeca.domain.decision import ModelDecision
from aeca.domain.secret import SecretStr

pytestmark = pytest.mark.eval


def test_one_real_decision_call_returns_a_decision_and_costs_something(api_key: SecretStr) -> None:
    facts = make_facts(
        subject="Contract review",
        text="Hi, please review the attached contract and send me your comments by Friday.",
    )
    choice = load_models().decision
    now = datetime(2026, 11, 7, 12, 0, tzinfo=UTC)
    request = LLMRequest(
        model=choice.model,
        effort=choice.effort,
        system=system_prompt(),
        messages=(
            {
                "role": "user",
                "content": first_user_message(facts, now=now, zone=ZoneInfo("Europe/Bucharest")),
            },
        ),
    )
    client = AnthropicClient.from_api_key(api_key, RecordingTracer())
    decision, response = client.parse(request, ModelDecision)
    assert isinstance(decision, ModelDecision)
    assert response.cost_usd > 0
