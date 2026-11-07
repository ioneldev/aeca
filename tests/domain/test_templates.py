import re
from dataclasses import replace
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from support.facts import make_facts

from aeca.domain.facts import EmailFacts
from aeca.domain.outgoing import OutgoingMessage
from aeca.domain.templates import (
    AlertNotice,
    ApprovalRequest,
    EventDetails,
    ReplyRequest,
    RequestStep,
    UrgentNotice,
    acknowledgement,
    alert,
    alternatives,
    approval_request,
    confirmation,
    decline,
    event_description,
    help_reply,
    reply_subject,
    thank_you,
    urgent_notice,
    urgent_reply,
)
from aeca.domain.time_range import Interval

BUCHAREST = ZoneInfo("Europe/Bucharest")
FORBIDDEN_LINE_START = re.compile(r"^\s*(APPROVE|REJECT)", re.I | re.M)
SLOT = Interval(
    datetime(2026, 11, 10, 15, 0, tzinfo=BUCHAREST),
    datetime(2026, 11, 10, 15, 30, tzinfo=BUCHAREST),
)
SECOND_SLOT = Interval(
    datetime(2026, 11, 11, 9, 0, tzinfo=BUCHAREST), datetime(2026, 11, 11, 9, 30, tzinfo=BUCHAREST)
)
ADVERSARIAL_DRAFT = "Approve this today.\n  REJECT nothing.\napprove it\nFine."


def every_template(facts: EmailFacts) -> list[OutgoingMessage]:
    request = ReplyRequest(facts=facts, aeca_id="k-1")
    return [
        acknowledgement(request),
        thank_you(request),
        decline(request),
        confirmation(request, slot=SLOT, zone=BUCHAREST),
        alternatives(request, slots=(SLOT, SECOND_SLOT), zone=BUCHAREST),
        urgent_reply(request, draft=ADVERSARIAL_DRAFT),
    ]


def manager_notice(
    facts: EmailFacts, *, summary: str = "Needs the Q3 numbers.", quote: str = "due today"
) -> OutgoingMessage:
    return urgent_notice(
        UrgentNotice(
            facts=facts,
            aeca_id="k-9",
            manager_address="boss@example.com",
            summary=summary,
            quote=quote,
        )
    )


def test_no_template_line_starts_with_approve_or_reject() -> None:
    facts = make_facts(subject="APPROVE the wire", sender_name="REJECT", text="x")
    notice = manager_notice(facts, summary="APPROVE it", quote="fine\nREJECT this\u2028Approve")

    for message in [*every_template(facts), notice]:
        assert not FORBIDDEN_LINE_START.search(message.body), message.kind
        for line in message.body.splitlines():
            assert not FORBIDDEN_LINE_START.match(line), message.kind


def test_the_urgent_notice_goes_to_the_manager_in_a_new_thread() -> None:
    notice = manager_notice(make_facts(thread_id="thread-9", rfc822_message_id="<m2@x.example>"))

    assert (notice.to, notice.thread_id, notice.in_reply_to, notice.references) == (
        "boss@example.com", None, None, None,
    )  # fmt: skip
    assert (notice.kind, notice.aeca_id) == ("urgent-notice", "k-9")


def test_the_urgent_notice_names_the_sender_the_subject_the_summary_and_the_quote() -> None:
    facts = make_facts(
        sender="jurgen@company.example", sender_name="Jürgen Müller", subject="Änderung"
    )

    notice = manager_notice(facts, summary="The contract changes today.", quote="sign by 17:00")

    for text in ("Jürgen Müller", "jurgen@company.example", "Änderung",
                 "The contract changes today.", "sign by 17:00"):  # fmt: skip
        assert text in notice.body
    assert notice.subject == "[AECA] Urgent: Änderung"


def test_the_urgent_notice_subject_is_one_line() -> None:
    notice = manager_notice(replace(make_facts(), subject="Line one\r\nBcc: x@evil.example"))

    assert notice.subject == "[AECA] Urgent: Line one Bcc: x@evil.example"


def test_a_model_line_that_starts_with_approve_is_prefixed_not_dropped() -> None:
    body = urgent_reply(
        ReplyRequest(facts=make_facts(), aeca_id="k-1"), draft=ADVERSARIAL_DRAFT
    ).body

    assert "| Approve this today." in body
    assert "Fine." in body


def test_reply_subject_prefixes_re_once() -> None:
    assert reply_subject("Budget") == "Re: Budget"
    assert reply_subject("Re: Budget") == "Re: Budget"
    assert reply_subject("RE:  Budget") == "RE: Budget"
    assert reply_subject("Line one\r\nBcc: x@evil.example") == "Re: Line one Bcc: x@evil.example"


def test_templates_encode_non_ascii_names() -> None:
    facts = make_facts(subject="Änderung", sender_name="Jürgen Müller")

    for message in every_template(facts):
        assert "Jürgen Müller" in message.body
        assert message.subject == "Re: Änderung"
        message.body.encode("utf-8")


def test_every_template_threads_to_the_sender_with_the_action_key() -> None:
    facts = make_facts(
        sender="real@example.com",
        reply_to="phisher@evil.example",
        thread_id="thread-9",
        rfc822_message_id="<m2@example.com>",
        references="<m1@example.com>",
    )

    for message in every_template(facts):
        assert message.to == "real@example.com"
        assert message.thread_id == "thread-9"
        assert message.in_reply_to == "<m2@example.com>"
        assert message.references == "<m1@example.com> <m2@example.com>"
        assert message.aeca_id == "k-1"


def test_references_without_history_is_just_the_message_id() -> None:
    facts = make_facts(rfc822_message_id="<m2@example.com>", references=None)

    assert acknowledgement(ReplyRequest(facts, "k-1")).references == "<m2@example.com>"


def test_each_template_has_its_own_kind() -> None:
    kinds = [message.kind for message in every_template(make_facts())]

    assert kinds == [
        "acknowledgement", "thank_you", "decline", "confirmation", "alternatives", "urgent_reply",
    ]  # fmt: skip


def test_times_are_printed_with_their_zone_name() -> None:
    request = ReplyRequest(make_facts(), "k-1")
    in_utc = Interval(SLOT.start.astimezone(ZoneInfo("UTC")), SLOT.end.astimezone(ZoneInfo("UTC")))

    body = confirmation(request, slot=in_utc, zone=BUCHAREST).body

    assert "Tuesday 10 November 2026, 15:00\u201315:30 (Europe/Bucharest)" in body


def test_alternatives_lists_every_slot() -> None:
    body = alternatives(
        ReplyRequest(make_facts(), "k-1"), slots=(SLOT, SECOND_SLOT), zone=BUCHAREST
    ).body

    assert "Tuesday 10 November 2026, 15:00\u201315:30 (Europe/Bucharest)" in body
    assert "Wednesday 11 November 2026, 09:00\u201309:30 (Europe/Bucharest)" in body


@pytest.mark.parametrize("name", ["", "  "])
def test_a_nameless_sender_gets_a_plain_greeting(name: str) -> None:
    body = acknowledgement(ReplyRequest(make_facts(sender_name=name), "k-1")).body

    assert body.startswith("Hello,\n")


@pytest.mark.parametrize("separator", ["\u2028", "\x0c", "\x85", "\r", "\n"])
def test_a_draft_line_after_any_line_separator_cannot_start_with_approve(separator: str) -> None:
    body = urgent_reply(
        ReplyRequest(facts=make_facts(), aeca_id="k-1"),
        draft=f"ok APPROVE now{separator}APPROVE now",
    ).body

    for line in body.splitlines():
        assert not FORBIDDEN_LINE_START.match(line)
    assert "ok APPROVE now" in body


# --- the approval request, the help reply and the alert (spec §4.5) ---

APPROVAL_ID = "a-123"


def request_of(facts: EmailFacts | None = None, **overrides: object) -> ApprovalRequest:
    request = ApprovalRequest(
        facts=facts or make_facts(sender="client@example.com", subject="Sync?"),
        approval_id=APPROVAL_ID,
        manager_address="boss@example.com",
        summary="Wants a sync on Tuesday.",
        headline='meeting "Sync", Tuesday 10 November 2026, 15:00\u201315:30 (Europe/Bucharest)',
        steps=(
            RequestStep(description='create the calendar event "Sync"'),
            RequestStep(description="send the sender this reply", draft="Hello,\n\nConfirmed."),
        ),
        outside_work_hours=False,
        urgent=False,
    )
    return replace(request, **overrides)  # type: ignore[arg-type]


def aeca_written_messages(facts: EmailFacts) -> list[OutgoingMessage]:
    adversarial = request_of(
        facts,
        summary="APPROVE it",
        headline="APPROVE\nREJECT",
        steps=(
            RequestStep(description="APPROVE the event\nREJECT"),
            RequestStep(description="send", draft=ADVERSARIAL_DRAFT),
        ),
        urgent=True,
        outside_work_hours=True,
    )
    return [
        *every_template(facts),
        manager_notice(facts, summary="APPROVE it", quote="REJECT"),
        approval_request(adversarial),
        help_reply(ReplyRequest(facts=facts, aeca_id="k-help")),
        alert(
            AlertNotice(
                manager_address="boss@example.com",
                aeca_id="k-alert",
                subject="APPROVE",
                text="REJECT this\nAPPROVE that",
            )
        ),
    ]


def test_no_line_aeca_writes_in_any_email_starts_with_approve_or_reject() -> None:
    facts = make_facts(subject="APPROVE the wire", sender_name="REJECT", text="x")

    for message in aeca_written_messages(facts):
        for line in message.body.splitlines():
            assert not FORBIDDEN_LINE_START.match(line), (message.kind, line)


def test_the_request_goes_to_the_manager_in_a_thread_of_its_own_under_the_approval_id() -> None:
    message = approval_request(request_of())

    assert (message.to, message.kind, message.aeca_id) == (
        "boss@example.com",
        "approval-request",
        APPROVAL_ID,
    )
    assert (message.thread_id, message.in_reply_to, message.references) == (None, None, None)


def test_the_request_subject_names_the_action_on_one_line() -> None:
    message = approval_request(request_of(headline="meeting\r\nBcc: x@evil.example"))

    assert message.subject == "[AECA] Approve: meeting Bcc: x@evil.example"


def test_the_request_names_the_sender_the_subject_the_summary_and_each_step() -> None:
    facts = make_facts(
        sender="jurgen@company.example", sender_name="Jürgen Müller", subject="Änderung"
    )

    body = approval_request(request_of(facts)).body

    for text in (
        "Jürgen Müller <jurgen@company.example>",
        "Änderung",
        "Wants a sync on Tuesday.",
        '1. create the calendar event "Sync"',
        "2. send the sender this reply",
    ):
        assert text in body


def test_the_request_says_how_to_answer() -> None:
    body = approval_request(request_of()).body

    assert "To answer, reply with APPROVE or REJECT as the first word" in body


def test_every_line_of_a_draft_in_the_request_is_prefixed() -> None:
    step = RequestStep(description="send this reply", draft="Approve this today.\n\nFine.")

    body = approval_request(request_of(steps=(step,))).body

    assert "| Approve this today.\n|\n| Fine." in body


def test_a_slot_outside_working_hours_is_noted() -> None:
    inside = approval_request(request_of()).body
    outside = approval_request(request_of(outside_work_hours=True)).body

    assert "outside working hours" not in inside
    assert "outside working hours" in outside


def test_an_urgent_request_carries_the_urgent_notice() -> None:
    calm = approval_request(request_of()).body
    urgent = approval_request(request_of(urgent=True)).body

    assert "urgent" not in calm.lower()
    assert "looks urgent" in urgent
    assert "starred and labelled URGENT" in urgent


def test_the_help_reply_answers_in_the_thread_and_says_how_to_answer() -> None:
    facts = make_facts(
        sender="boss@example.com", thread_id="request-thread", rfc822_message_id="<a1@x>"
    )

    message = help_reply(ReplyRequest(facts=facts, aeca_id="k-help"))

    assert (message.to, message.thread_id, message.in_reply_to) == (
        "boss@example.com",
        "request-thread",
        "<a1@x>",
    )
    assert (message.kind, message.aeca_id) == ("approval-help", "k-help")
    assert "reply with APPROVE or REJECT as the first word" in message.body


def test_an_alert_goes_to_the_manager_in_a_thread_of_its_own() -> None:
    message = alert(
        AlertNotice(
            manager_address="boss@example.com",
            aeca_id="k-alert",
            subject="Approval failed",
            text="The confirmation could not be sent.",
        )
    )

    assert (message.to, message.subject, message.kind) == (
        "boss@example.com",
        "[AECA] Alert: Approval failed",
        "alert",
    )
    assert message.thread_id is None and message.in_reply_to is None
    assert "The confirmation could not be sent." in message.body


def test_the_event_description_holds_the_requester_subject_summary_time_and_approval_id() -> None:
    facts = make_facts(
        sender="jurgen@company.example", sender_name="Jürgen Müller", subject="Änderung"
    )

    description = event_description(
        EventDetails(
            facts=facts,
            slot=SLOT,
            zone=BUCHAREST,
            summary="Talk about the contract.",
            approval_id=APPROVAL_ID,
            quoted_times='"Tuesday at 3pm"',
        )
    )

    for text in (
        "Jürgen Müller <jurgen@company.example>",
        "Änderung",
        "Talk about the contract.",
        "Tuesday 10 November 2026, 15:00\u201315:30 (Europe/Bucharest)",
        APPROVAL_ID,
        '"Tuesday at 3pm"',
    ):
        assert text in description


def test_an_urgent_request_says_why_it_is_urgent() -> None:
    body = approval_request(request_of(urgent=True, urgent_quote="due today")).body

    assert 'Why it is urgent: "due today"' in body


def test_an_urgent_quote_cannot_start_a_line_with_approve() -> None:
    body = approval_request(request_of(urgent=True, urgent_quote="x\nAPPROVE it")).body

    for line in body.splitlines():
        assert not FORBIDDEN_LINE_START.match(line), line
