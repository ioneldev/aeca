"""One bad email never stops the loop, a system-wide error charges no email, and what an email
cost reaches its audit line. Carried over from the thin path, now through the full flow."""

from decimal import Decimal
from pathlib import Path

import pytest
from support.decisions import decision_of
from support.inbox_rig import ACCOUNT, InboxRig

from aeca.application.decide import BudgetExhausted
from aeca.application.ports import ProviderError
from aeca.application.process_email import MAX_TRIES
from aeca.domain.actions import ActionStatus
from aeca.domain.errors import CredentialsRevoked, EmailError
from aeca.domain.labels import ERROR, PROCESSED, Label


@pytest.fixture
def rig(tmp_path: Path) -> InboxRig:
    return InboxRig(tmp_path)


def test_mail_the_account_sends_to_itself_without_aeca_headers_is_classified(
    rig: InboxRig,
) -> None:
    message_id = rig.receive(sender=ACCOUNT, label_ids=("INBOX", "SENT"))

    assert rig.processor.process(message_id) is not None
    assert len(rig.decided) == 1


def test_a_message_already_processed_is_skipped_after_a_fresh_get(rig: InboxRig) -> None:
    message_id = rig.receive(label_ids=("INBOX", PROCESSED))

    assert rig.processor.process(message_id) is None
    assert rig.mailbox.get_log == [message_id]
    assert rig.decided == []
    assert rig.mailbox.modify_log == []


def test_the_audit_line_carries_what_the_decision_cost(rig: InboxRig) -> None:
    rig.decisions_cost_money = True

    line = rig.processor.process(rig.receive())

    assert line is not None
    assert (line.tokens_in, line.tokens_out, line.usd) == (1000, 100, Decimal("0.01"))


def test_a_decision_without_a_model_call_costs_nothing(rig: InboxRig) -> None:
    line = rig.processor.process(rig.receive())

    assert line is not None
    assert (line.tokens_in, line.tokens_out, line.usd) == (0, 0, Decimal(0))


def test_an_email_error_adds_error_counts_an_attempt_and_audits_a_failure(rig: InboxRig) -> None:
    rig.next_decision = EmailError("cannot parse", next_action="none")
    message_id = rig.receive(subject="Broken")

    line = rig.processor.process(message_id)

    assert rig.mailbox.added_labels(message_id) == [ERROR]
    assert line is not None
    assert (line.outcome, line.error, line.subject) == ("failed", "cannot parse", "Broken")
    assert rig.audit.lines == [line]


def test_an_email_failing_three_times_gets_error_and_processed(rig: InboxRig) -> None:
    rig.next_decision = EmailError("cannot parse", next_action="none")
    message_id = rig.receive()

    for _ in range(MAX_TRIES):
        rig.processor.process(message_id)

    assert rig.mailbox.added_labels(message_id) == [ERROR, ERROR, ERROR, PROCESSED]


def test_a_provider_error_specific_to_one_email_counts_like_an_email_error(rig: InboxRig) -> None:
    rig.next_decision = ProviderError(
        "bad request", next_action="none", kind="invalid_request", system_wide=False
    )
    message_id = rig.receive()

    line = rig.processor.process(message_id)

    assert line is not None and line.outcome == "failed"
    assert rig.mailbox.added_labels(message_id) == [ERROR]


def test_an_unexpected_exception_fails_that_email_and_not_the_service(rig: InboxRig) -> None:
    rig.next_decision = KeyError("boom")
    message_id = rig.receive()

    line = rig.processor.process(message_id)

    assert line is not None and line.outcome == "failed"
    assert rig.mailbox.added_labels(message_id) == [ERROR]


def test_a_failure_still_reports_what_a_paid_call_cost(rig: InboxRig) -> None:
    rig.decisions_cost_money = True
    rig.next_decision = EmailError("after the call", next_action="none")

    line = rig.processor.process(rig.receive())

    assert line is not None and line.usd == Decimal("0.01")


@pytest.mark.parametrize(
    "error",
    [
        CredentialsRevoked("revoked", next_action="none"),
        ProviderError("down", next_action="none", kind="overloaded", system_wide=True),
        BudgetExhausted(spent_usd=Decimal(5)),
    ],
    ids=["credentials", "provider-outage", "budget"],
)
def test_a_system_wide_error_charges_the_email_nothing(rig: InboxRig, error: Exception) -> None:
    rig.next_decision = error
    message_id = rig.receive()

    with pytest.raises(type(error)):
        rig.processor.process(message_id)

    assert rig.mailbox.modify_log == []
    assert rig.audit.lines == []
    assert rig.store.attempts_increment(message_id) == 1


def test_a_system_wide_error_while_acting_leaves_the_action_to_resume(rig: InboxRig) -> None:
    message_id = rig.receive()
    rig.mailbox.modify_failures.append(CredentialsRevoked("revoked", next_action="none"))

    with pytest.raises(CredentialsRevoked):
        rig.processor.process(message_id)

    entry = rig.store.journal_get(message_id)
    assert entry is not None
    record = rig.store.action_get(entry.actions[0].key)
    assert record is not None and record.status is ActionStatus.PENDING
    assert rig.audit.lines == []
    assert rig.processor.unfinished() == [message_id]


def test_an_email_that_failed_before_loses_error_in_the_modify_that_adds_processed(
    rig: InboxRig,
) -> None:
    message_id = rig.receive(label_ids=("INBOX", ERROR))

    rig.processor.process(message_id)

    last = rig.mailbox.modify_log[-1]
    assert (last.add, last.remove) == ((PROCESSED,), (ERROR,))
    assert rig.mailbox.labels_of(message_id) == {"INBOX", "TASK", PROCESSED}


def test_an_email_that_never_failed_removes_nothing(rig: InboxRig) -> None:
    rig.processor.process(rig.receive())

    assert all(call.remove == () for call in rig.mailbox.modify_log)


def test_an_email_that_cannot_be_fetched_cannot_be_labelled_so_the_failure_is_raised(
    rig: InboxRig,
) -> None:
    rig.mailbox.get_failures["gone"] = EmailError("no such message", next_action="none")

    with pytest.raises(EmailError, match="gone"):
        rig.processor.process("gone")

    assert rig.store.attempts_increment("gone") == 2
    assert [(line.outcome, line.subject) for line in rig.audit.lines] == [("failed", "")]


def test_a_failure_while_recording_a_failure_is_raised_as_an_email_error(rig: InboxRig) -> None:
    rig.next_decision = EmailError("cannot parse", next_action="none")
    rig.audit.failure = OSError("disk full")

    with pytest.raises(EmailError, match="disk full"):
        rig.processor.process(rig.receive())


def test_a_system_wide_error_while_recording_a_failure_is_not_hidden(rig: InboxRig) -> None:
    rig.next_decision = EmailError("cannot parse", next_action="none")
    rig.audit.failure = CredentialsRevoked("revoked", next_action="none")

    with pytest.raises(CredentialsRevoked):
        rig.processor.process(rig.receive())


def test_a_decision_of_the_rule_reaches_the_audit_line(rig: InboxRig) -> None:
    rig.next_decision = decision_of(Label.SPAM, rules_fired=("dmarc_fail_and_link_mismatch",))

    line = rig.processor.process(rig.receive())

    assert line is not None and line.rules == ("dmarc_fail_and_link_mismatch",)
