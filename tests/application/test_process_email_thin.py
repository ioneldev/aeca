from decimal import Decimal

import pytest
from fakes.fake_mailbox import FakeMailbox
from fakes.fixed_clock import FIXED_NOW, FixedClock
from fakes.memory_audit import MemoryAudit
from fakes.memory_state import MemoryState
from fakes.recording_tracer import RecordingTracer
from fakes.scripted_model import response_with_text
from support.emails import make_raw

from aeca.application.budget import Budget
from aeca.application.decide import BudgetExhausted
from aeca.application.ports import ProviderError
from aeca.application.process_email import MAX_TRIES, InboxDeps, InboxProcessor
from aeca.domain.approval import Approval, ApprovalState
from aeca.domain.decision import Decision
from aeca.domain.errors import CredentialsRevoked, EmailError
from aeca.domain.facts import EmailFacts, FactSettings
from aeca.domain.labels import ERROR, PROCESSED, Label, ReplyKind

ACCOUNT = "aeca@gmail.com"


def decision_of(label: Label, *, urgent: bool = False, finance: bool = False) -> Decision:
    return Decision(
        label=label,
        urgent=urgent,
        finance=finance,
        meeting=None,
        reply=ReplyKind.NONE,
        urgent_draft=None,
        summary="s",
        suspected_injection=None,
        decided_by=Decision.fallback("x").decided_by,
        rules_fired=("r1",),
        dropped=(),
    )


class Harness:
    def __init__(self) -> None:
        self.mailbox = FakeMailbox(ACCOUNT)
        self.store = MemoryState()
        self.audit = MemoryAudit()
        self.budget = Budget(Decimal(5))
        self.decided: list[EmailFacts] = []
        self.next_decision: Decision | Exception = decision_of(Label.TASK)
        self.cost_of_decision = False
        self.processor = InboxProcessor(
            InboxDeps(
                mailbox=self.mailbox,
                store=self.store,
                audit=self.audit,
                tracer=RecordingTracer(),
                decide=self._decide,
                facts=FactSettings(
                    account_address=ACCOUNT,
                    internal_senders=frozenset(),
                    bulk_senders=frozenset(),
                ),
                clock=FixedClock(),
                budget=self.budget,
            )
        )

    def _decide(self, facts: EmailFacts) -> Decision:
        self.decided.append(facts)
        if self.cost_of_decision:
            self.budget.charge(response_with_text("paid"))
        if isinstance(self.next_decision, Exception):
            raise self.next_decision
        return self.next_decision


@pytest.fixture
def harness() -> Harness:
    return Harness()


def receive(harness: Harness, **kwargs: object) -> str:
    raw = make_raw(**kwargs)  # type: ignore[arg-type]
    harness.mailbox.add(raw)
    return raw.message_id


def test_a_new_email_gets_its_label_an_audit_line_then_processed_last(harness: Harness) -> None:
    message_id = receive(harness, subject="Please review")

    line = harness.processor.process(message_id)

    assert harness.mailbox.modify_log[-1].add == (PROCESSED,)
    assert harness.mailbox.modify_log[0].add == ("TASK",)
    assert harness.audit.lines == [line]
    assert line is not None
    assert (line.outcome, line.label, line.subject, line.time) == (
        "done",
        "TASK",
        "Please review",
        FIXED_NOW,
    )
    assert line.rules == ("r1",)


def test_an_urgent_email_is_starred_and_labelled_urgent_before_processed(harness: Harness) -> None:
    harness.next_decision = decision_of(Label.TASK, urgent=True)
    message_id = receive(harness)

    harness.processor.process(message_id)

    assert harness.mailbox.added_labels(message_id) == ["TASK", "STARRED", "URGENT", PROCESSED]


def test_a_finance_email_gets_the_finance_label(harness: Harness) -> None:
    harness.next_decision = decision_of(Label.INFO_ONLY, finance=True)
    message_id = receive(harness)

    harness.processor.process(message_id)

    assert harness.mailbox.added_labels(message_id) == ["INFO_ONLY", "Finance", PROCESSED]


def test_spam_goes_to_the_system_spam_label_and_inbox_is_not_removed_separately(
    harness: Harness,
) -> None:
    harness.next_decision = decision_of(Label.SPAM)
    message_id = receive(harness)

    harness.processor.process(message_id)

    assert harness.mailbox.added_labels(message_id) == ["SPAM", PROCESSED]
    assert all(call.remove == () for call in harness.mailbox.modify_log)


def test_own_mail_is_marked_processed_without_a_model_call(harness: Harness) -> None:
    message_id = receive(
        harness,
        sender=ACCOUNT,
        headers={"X-AECA-Kind": "reply", "X-AECA-Id": "abc"},
        label_ids=("INBOX", "SENT"),
    )

    line = harness.processor.process(message_id)

    assert line is None
    assert harness.decided == []
    assert harness.audit.lines == []
    assert harness.mailbox.added_labels(message_id) == [PROCESSED]


def test_mail_the_account_sends_to_itself_without_aeca_headers_is_classified(
    harness: Harness,
) -> None:
    message_id = receive(harness, sender=ACCOUNT, label_ids=("INBOX", "SENT"))

    assert harness.processor.process(message_id) is not None
    assert len(harness.decided) == 1


def test_an_approval_thread_is_skipped_with_no_model_call_and_no_audit_line(
    harness: Harness,
) -> None:
    message_id = receive(harness, thread_id="request-thread")
    harness.store.approvals_by_thread["request-thread"] = Approval(
        id="a1",
        source_message_id="m0",
        source_thread_id="t0",
        attempt=1,
        actions=(),
        state=ApprovalState.AWAITING,
        request_thread_id="request-thread",
        summary="s",
        holds=(),
    )

    assert harness.processor.process(message_id) is None
    assert harness.decided == []
    assert harness.audit.lines == []


def test_a_message_already_processed_is_skipped_after_a_fresh_get(harness: Harness) -> None:
    message_id = receive(harness, label_ids=("INBOX", PROCESSED))
    harness.mailbox.listing_override = [message_id]

    assert harness.processor.process(message_id) is None
    assert harness.mailbox.get_log == [message_id]
    assert harness.decided == []
    assert harness.mailbox.modify_log == []


def test_the_audit_line_carries_what_the_decision_cost(harness: Harness) -> None:
    harness.cost_of_decision = True
    message_id = receive(harness)

    line = harness.processor.process(message_id)

    assert line is not None
    assert (line.tokens_in, line.tokens_out, line.usd) == (1000, 100, Decimal("0.01"))


def test_a_rule_decision_costs_nothing(harness: Harness) -> None:
    message_id = receive(harness)

    line = harness.processor.process(message_id)

    assert line is not None
    assert (line.tokens_in, line.tokens_out, line.usd) == (0, 0, Decimal(0))


def test_an_email_error_adds_error_counts_an_attempt_and_audits_a_failure(
    harness: Harness,
) -> None:
    harness.next_decision = EmailError("cannot parse", next_action="none")
    message_id = receive(harness, subject="Broken")

    line = harness.processor.process(message_id)

    assert harness.mailbox.added_labels(message_id) == [ERROR]
    assert line is not None
    assert (line.outcome, line.error, line.subject) == ("failed", "cannot parse", "Broken")
    assert harness.audit.lines == [line]


def test_the_third_failure_also_adds_processed(harness: Harness) -> None:
    harness.next_decision = EmailError("cannot parse", next_action="none")
    message_id = receive(harness)

    for _ in range(MAX_TRIES):
        harness.processor.process(message_id)

    assert harness.mailbox.added_labels(message_id) == [ERROR, ERROR, ERROR, PROCESSED]


def test_a_provider_error_specific_to_one_email_counts_like_an_email_error(
    harness: Harness,
) -> None:
    harness.next_decision = ProviderError(
        "bad request", next_action="none", kind="invalid_request", system_wide=False
    )
    message_id = receive(harness)

    line = harness.processor.process(message_id)

    assert line is not None
    assert line.outcome == "failed"
    assert harness.mailbox.added_labels(message_id) == [ERROR]


def test_an_unexpected_exception_fails_that_email_and_not_the_service(harness: Harness) -> None:
    harness.next_decision = KeyError("boom")
    message_id = receive(harness)

    line = harness.processor.process(message_id)

    assert line is not None
    assert line.outcome == "failed"
    assert harness.mailbox.added_labels(message_id) == [ERROR]


def test_a_failure_still_reports_what_a_paid_call_cost(harness: Harness) -> None:
    harness.cost_of_decision = True
    harness.next_decision = EmailError("after the call", next_action="none")
    message_id = receive(harness)

    line = harness.processor.process(message_id)

    assert line is not None
    assert line.usd == Decimal("0.01")


@pytest.mark.parametrize(
    "error",
    [
        CredentialsRevoked("revoked", next_action="none"),
        ProviderError("down", next_action="none", kind="overloaded", system_wide=True),
        BudgetExhausted(spent_usd=Decimal(5)),
    ],
    ids=["credentials", "provider-outage", "budget"],
)
def test_a_system_wide_error_charges_the_email_nothing(harness: Harness, error: Exception) -> None:
    harness.next_decision = error
    message_id = receive(harness)

    with pytest.raises(type(error)):
        harness.processor.process(message_id)

    assert harness.mailbox.modify_log == []
    assert harness.audit.lines == []
    assert harness.store.attempts_increment(message_id) == 1


def test_an_email_that_cannot_be_fetched_fails_with_an_empty_audit_subject(
    harness: Harness,
) -> None:
    harness.mailbox.get_failures["gone"] = EmailError("no such message", next_action="none")

    line = harness.processor.process("gone")

    assert line is not None
    assert (line.outcome, line.subject, line.sender) == ("failed", "", "")
