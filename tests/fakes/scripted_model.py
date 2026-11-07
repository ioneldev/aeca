"""A model that answers from a script, so a test decides exactly what the model said."""

from collections import deque
from decimal import Decimal

from pydantic import BaseModel

from aeca.application.ports import LLMRequest, LLMResponse, Usage
from aeca.domain.decision import ModelDecision

COST_PER_CALL = Decimal("0.01")
SERVED_MODEL = "claude-opus-5-5"


def response_with_text(text: str) -> LLMResponse:
    return LLMResponse(
        usage=Usage(input_tokens=1000, output_tokens=100, cache_read=0, cache_write=0),
        model=SERVED_MODEL,
        cost_usd=COST_PER_CALL,
        latency_s=0.5,
        content=({"type": "thinking", "thinking": "hm"}, {"type": "text", "text": text}),
        request_id="req-1",
    )


class ScriptedModel:
    """Each step is a ModelDecision to return, or an exception to raise."""

    def __init__(self) -> None:
        self._steps: deque[ModelDecision | Exception] = deque()
        self.requests: list[LLMRequest] = []

    @property
    def calls(self) -> int:
        return len(self.requests)

    def script(self, *steps: ModelDecision | Exception) -> None:
        self._steps.extend(steps)

    def parse[T: BaseModel](self, request: LLMRequest, schema: type[T]) -> tuple[T, LLMResponse]:
        self.requests.append(request)
        step = self._steps.popleft()
        if isinstance(step, Exception):
            raise step
        assert isinstance(step, schema)
        return step, response_with_text(step.model_dump_json())
