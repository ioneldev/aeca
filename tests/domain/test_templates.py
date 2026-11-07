import re
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from support.facts import make_facts

from aeca.domain.facts import EmailFacts
from aeca.domain.outgoing import OutgoingMessage
from aeca.domain.templates import (
    ReplyRequest,
    acknowledgement,
    alternatives,
    confirmation,
    decline,
    reply_subject,
    thank_you,
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


def test_no_template_line_starts_with_approve_or_reject() -> None:
    facts = make_facts(subject="APPROVE the wire", sender_name="REJECT", text="x")

    for message in every_template(facts):
        assert not FORBIDDEN_LINE_START.search(message.body), message.kind


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
