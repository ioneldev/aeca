"""Decides one email: rules that decide alone first, then one model call that code validates."""

import json
from dataclasses import dataclass, field
from decimal import Decimal
from zoneinfo import ZoneInfo

from aeca.application.budget import Budget
from aeca.application.decision_prompt import first_user_message, system_prompt
from aeca.application.ports import (
    Clock,
    LLMClient,
    LLMRequest,
    PaidCall,
    ProviderError,
    ReplyUnusable,
    Tracer,
)
from aeca.domain.decision import DecidedBy, Decision, ModelDecision
from aeca.domain.errors import AecaError
from aeca.domain.facts import EmailFacts
from aeca.domain.labels import Label, ReplyKind
from aeca.domain.rules import RuleVerdict, rule_verdict
from aeca.domain.text import bounded, visible
from aeca.domain.validation import LabelRefused, ValidationContext, validate

MAX_CORRECTIONS = 2
LABEL_ECHO_LIMIT = 50
REFUSED = "refused"
LABEL_CHOICES = ", ".join(label.value for label in Label)
UNUSABLE_SUMMARY = "The model gave no valid label, so the email is left for the manager."
REFUSED_SUMMARY = "The model declined to read this email, so it is left for the manager."


class BudgetExhausted(AecaError):
    def __init__(self, *, spent_usd: Decimal) -> None:
        super().__init__(
            f"The day's model budget is spent (${spent_usd:.4f})",
            next_action="Raise MAX_USD_PER_DAY, or wait until tomorrow: emails wait until then",
        )
        self.spent_usd = spent_usd


@dataclass(frozen=True, slots=True)
class DecideDeps:
    model: LLMClient
    model_name: str
    effort: str
    clock: Clock
    zone: ZoneInfo
    tracer: Tracer
    budget: Budget


def decide(facts: EmailFacts, deps: DecideDeps) -> Decision:
    verdict = rule_verdict(facts)
    if verdict is not None:
        return _decision_of_rule(verdict)
    return _ModelConversation(facts, deps).decide()


def _decision_of_rule(verdict: RuleVerdict) -> Decision:
    return Decision(
        label=verdict.label,
        urgent=False,
        finance=False,
        meeting=None,
        reply=ReplyKind.NONE,
        urgent_draft=None,
        summary=f"Decided by the rule {verdict.rule}.",
        suspected_injection=None,
        decided_by=DecidedBy.RULE,
        rules_fired=(verdict.rule,),
        dropped=(),
    )


@dataclass(frozen=True, slots=True)
class _Correction:
    """The model answered, but not with something to accept."""

    problem: str
    assistant_content: tuple[object, ...]


@dataclass(slots=True)
class _ModelConversation:
    """The history is append-only: a correction adds two turns and edits none."""

    facts: EmailFacts
    deps: DecideDeps
    messages: list[dict[str, object]] = field(default_factory=list)

    def decide(self) -> Decision:
        self.messages.append({"role": "user", "content": self._first_message()})
        self._debug_the_email()
        for attempt_index in range(MAX_CORRECTIONS + 1):
            outcome = self._ask(attempt_index)
            if isinstance(outcome, Decision):
                return outcome
            if attempt_index == MAX_CORRECTIONS:
                break
            self._trace_correction(outcome, attempt_index)
            self._append_correction(outcome)
        return Decision.fallback(UNUSABLE_SUMMARY)

    def _first_message(self) -> str:
        return first_user_message(self.facts, now=self.deps.clock.now(), zone=self.deps.zone)

    def _ask(self, attempt_index: int) -> Decision | _Correction:
        self._refuse_if_budget_is_spent()
        try:
            submitted, response = self.deps.model.parse(self._request(), ModelDecision)
        except ReplyUnusable as unusable:
            self._charge(unusable.response)
            self._debug_the_reply(unusable.response.content, attempt_index)
            return _Correction(_schema_problem(unusable.problem), unusable.response.content)
        except ProviderError as error:
            return self._after_provider_error(error)
        self._charge(response)
        self._debug_the_reply(response.content, attempt_index)
        return self._validated(submitted, response.content)

    def _debug_the_email(self) -> None:
        text = self.messages[0]["content"]
        self.deps.tracer.debug(f"email {self.facts.message_id} as the model sees it:\n{text}")

    def _debug_the_reply(self, content: tuple[object, ...], attempt_index: int) -> None:
        reply = json.dumps(content, default=str)
        self.deps.tracer.debug(
            f"reply to {self.facts.message_id}, attempt {attempt_index}:\n{reply}"
        )

    def _refuse_if_budget_is_spent(self) -> None:
        if self.deps.budget.exhausted():
            raise BudgetExhausted(spent_usd=self.deps.budget.spent_usd)

    def _request(self) -> LLMRequest:
        return LLMRequest(
            model=self.deps.model_name,
            effort=self.deps.effort,
            system=system_prompt(),
            messages=tuple(self.messages),
        )

    def _after_provider_error(self, error: ProviderError) -> Decision:
        if error.paid_call is not None:
            self._charge(error.paid_call)
        if error.kind != REFUSED:
            raise error
        self.deps.tracer.event("model_refused", message_id=self.facts.message_id)
        return Decision.fallback(REFUSED_SUMMARY)

    def _validated(
        self, submitted: ModelDecision, assistant_content: tuple[object, ...]
    ) -> Decision | _Correction:
        context = ValidationContext(
            facts=self.facts, now=self.deps.clock.now(), zone=self.deps.zone
        )
        checked = validate(submitted, context)
        if isinstance(checked, LabelRefused):
            reason = _bounded_reason(checked.reason, submitted.primary_label)
            return _Correction(_label_problem(reason), assistant_content)
        self._trace_suspected_injection(checked)
        return checked

    def _charge(self, call: PaidCall) -> None:
        self.deps.budget.charge(call)
        self.deps.tracer.event(
            "model_call",
            message_id=self.facts.message_id,
            model=call.model,
            input_tokens=call.usage.input_tokens,
            output_tokens=call.usage.output_tokens,
            cache_read=call.usage.cache_read,
            cache_write=call.usage.cache_write,
            cost_usd=str(call.cost_usd),
            latency_s=call.latency_s,
        )

    # The text is email content, which the trace never holds; it goes to the debug log, where the
    # email itself is, and the trace keeps only that there was one.
    def _trace_suspected_injection(self, decision: Decision) -> None:
        text = decision.suspected_injection
        if text is None:
            return
        self.deps.tracer.event(
            "suspected_injection", message_id=self.facts.message_id, chars=len(text)
        )
        self.deps.tracer.debug(f"suspected injection in {self.facts.message_id}:\n{text}")

    def _trace_correction(self, correction: _Correction, attempt_index: int) -> None:
        self.deps.tracer.event(
            "correction",
            message_id=self.facts.message_id,
            attempt=attempt_index + 1,
            problem=correction.problem,
        )

    def _append_correction(self, correction: _Correction) -> None:
        self.messages.append({"role": "assistant", "content": correction.assistant_content})
        self.messages.append({"role": "user", "content": correction.problem})


# The refused label is the model's own words, possibly steered by the email, and the correction
# sits outside the fence, so it goes back short and with nothing invisible in it.
def _bounded_reason(reason: str, submitted_label: str) -> str:
    label = submitted_label.strip()
    if not label:
        return reason
    return reason.replace(label, bounded(visible(label), LABEL_ECHO_LIMIT))


def _label_problem(reason: str) -> str:
    return (
        f"Your answer was refused: {reason}. Answer again in full, and make primary_label "
        f"exactly one of: {LABEL_CHOICES}."
    )


def _schema_problem(problem: str) -> str:
    return f"Your answer did not match the decision schema: {problem}. Answer again in full."
