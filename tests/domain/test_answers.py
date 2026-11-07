"""Code reads the manager's answer, never the model: the first word of the first line not quoted
with `>`, and only from a reply that provably comes from the manager (spec §4.5)."""

from dataclasses import replace

import pytest
from support.emails import make_raw
from support.facts import make_facts

from aeca.domain.answers import (
    Answer,
    AnswerRules,
    answer_of,
    counts_as_answer,
    parse_answer,
    why_not_an_answer,
)
from aeca.domain.auth_results import AuthResults
from aeca.domain.facts import EmailFacts

ACCOUNT = "aeca.test@gmail.com"
MANAGER = "boss@example.com"
WITH_MANAGER = AnswerRules(account_address=ACCOUNT, manager_address=MANAGER)
WITHOUT_MANAGER = AnswerRules(account_address=ACCOUNT, manager_address=None)
PASS = AuthResults(spf="pass", dkim="pass", dmarc="pass", dmarc_domain="example.com")


@pytest.mark.parametrize(
    ("text", "answer"),
    [
        ("Approve.", "approve"),
        ("APPROVED", "approve"),
        ("\n\napprove", "approve"),
        (" Approve", "approve"),
        ("> old text\nAPPROVE", "approve"),
        ("Reject", "reject"),
        ("Yes", "unclear"),
    ],
)
def test_parse_answer(text: str, answer: str) -> None:
    assert parse_answer(text) == answer


@pytest.mark.parametrize(
    ("text", "answer"),
    [
        ("\u00a0APPROVE", Answer.APPROVE),
        ("\u200bApprove", Answer.APPROVE),
        ("\ufeff\u200d  reject", Answer.REJECT),
        ("\t\r\nRejected!", Answer.REJECT),
        ("approve, thanks\nREJECT", Answer.APPROVE),
        ("  > APPROVE\n\u00a0\nreject", Answer.REJECT),
        ("APPROVE\r\n\r\nOn Sat, AECA wrote:\r\n> REJECT", Answer.APPROVE),
    ],
)
def test_leading_whitespace_invisible_characters_and_quotes_are_skipped(
    text: str, answer: Answer
) -> None:
    assert parse_answer(text) is answer


@pytest.mark.parametrize(
    "text",
    [
        "",
        "\n \n",
        "> APPROVE",
        "| Approve this today.",
        "Approval",
        "Approves",
        "Don't approve",
        "APPROVE/REJECT",
        "Approve-ish",
        "Approve123",
        "*APPROVE*",
        "-APPROVE",
        "No",
    ],
)
def test_anything_but_the_two_words_is_unclear(text: str) -> None:
    assert parse_answer(text) is Answer.UNCLEAR


def test_an_html_only_reply_is_read() -> None:
    raw = make_raw(
        body=None, html="<div dir='ltr'>&nbsp;APPROVE</div><blockquote>REJECT</blockquote>"
    )

    assert answer_of(raw) is Answer.APPROVE


def test_the_plain_part_is_read_before_the_html() -> None:
    raw = make_raw(body="REJECT", html="<p>APPROVE</p>")

    assert answer_of(raw) is Answer.REJECT


def test_a_zero_width_space_in_the_raw_body_is_skipped_although_facts_would_escape_it() -> None:
    raw = make_raw(body="\u200bAPPROVE")

    assert answer_of(raw) is Answer.APPROVE


def reply(**overrides: object) -> EmailFacts:
    base = make_facts(sender=MANAGER, label_ids=frozenset({"INBOX"}))
    fields: dict[str, object] = {"auth": PASS, "aeca_kind": None, "aeca_id": None, **overrides}
    return replace(base, **fields)  # type: ignore[arg-type]


def sent_by_account(**overrides: object) -> EmailFacts:
    return reply(sender=ACCOUNT, label_ids=frozenset({"SENT", "INBOX"}), **overrides)


@pytest.mark.parametrize("rules", [WITH_MANAGER, WITHOUT_MANAGER])
def test_a_sent_reply_counts_with_or_without_a_manager_address(rules: AnswerRules) -> None:
    assert counts_as_answer(sent_by_account(), rules)


@pytest.mark.parametrize("sender", [MANAGER, "Boss@Example.COM", " boss@example.com "])
def test_a_manager_reply_with_dmarc_pass_counts(sender: str) -> None:
    assert counts_as_answer(reply(sender=sender), WITH_MANAGER)


def test_a_gmail_manager_counts_whatever_its_dots_and_case() -> None:
    rules = AnswerRules(account_address=ACCOUNT, manager_address="the.boss@gmail.com")
    gmail_pass = AuthResults(spf="pass", dkim="pass", dmarc="pass", dmarc_domain="gmail.com")

    assert counts_as_answer(reply(sender="TheBoss@gmail.com", auth=gmail_pass), rules)


@pytest.mark.parametrize("dmarc", ["fail", "none", "temperror", None])
def test_a_manager_reply_without_dmarc_pass_does_not_count(dmarc: str | None) -> None:
    message = reply(
        auth=AuthResults(spf="pass", dkim="pass", dmarc=dmarc, dmarc_domain="example.com")
    )

    assert not counts_as_answer(message, WITH_MANAGER)
    assert why_not_an_answer(message, WITH_MANAGER) == "manager_dmarc_not_pass"


def test_without_a_manager_address_only_a_sent_reply_counts() -> None:
    assert not counts_as_answer(reply(), WITHOUT_MANAGER)
    assert why_not_an_answer(reply(), WITHOUT_MANAGER) == "not_sent_by_the_account"


def test_a_reply_from_anyone_else_does_not_count_even_with_dmarc_pass() -> None:
    message = reply(sender="someone@example.com")

    assert not counts_as_answer(message, WITH_MANAGER)
    assert why_not_an_answer(message, WITH_MANAGER) == "not_from_the_manager"


@pytest.mark.parametrize(
    "rules",
    [
        WITH_MANAGER,
        WITHOUT_MANAGER,
        AnswerRules(account_address=ACCOUNT, manager_address=ACCOUNT),
        AnswerRules(account_address=ACCOUNT, manager_address="aecatest@gmail.com"),
    ],
)
def test_the_account_address_without_sent_never_counts_even_with_dmarc_pass(
    rules: AnswerRules,
) -> None:
    spoofed = reply(sender="aeca.test@gmail.com")
    dotless = reply(sender="AECATEST@gmail.com")

    assert not counts_as_answer(spoofed, rules)
    assert not counts_as_answer(dotless, rules)


@pytest.mark.parametrize(
    ("kind", "aeca_id"), [("approval-request", "a-1"), (None, "a-1"), ("approval-help", None)]
)
def test_a_message_with_any_aeca_header_never_counts(kind: str | None, aeca_id: str | None) -> None:
    for message in (
        sent_by_account(aeca_kind=kind, aeca_id=aeca_id),
        reply(aeca_kind=kind, aeca_id=aeca_id),
    ):
        assert not counts_as_answer(message, WITH_MANAGER)
        assert why_not_an_answer(message, WITH_MANAGER) == "carries_aeca_headers"


def test_a_counted_reply_has_no_reason_against_it() -> None:
    assert why_not_an_answer(sent_by_account(), WITHOUT_MANAGER) is None
    assert why_not_an_answer(reply(), WITH_MANAGER) is None


# A DMARC pass proves only the domain Gmail judged it for, and only when AECA reads the same From.


@pytest.mark.parametrize("domain", ["evil.example", "mail.example.com", None])
def test_a_manager_reply_whose_dmarc_pass_is_for_another_domain_does_not_count(
    domain: str | None,
) -> None:
    message = reply(auth=AuthResults(spf="pass", dkim="pass", dmarc="pass", dmarc_domain=domain))

    assert not counts_as_answer(message, WITH_MANAGER)
    assert why_not_an_answer(message, WITH_MANAGER) == "manager_dmarc_for_another_domain"


def test_a_manager_reply_whose_sender_is_ambiguous_does_not_count() -> None:
    message = reply(single_sender=False)

    assert not counts_as_answer(message, WITH_MANAGER)
    assert why_not_an_answer(message, WITH_MANAGER) == "sender_ambiguous"


# Bottom-posting: an attribution line directly above a quote is not the answer (controller ruling).


@pytest.mark.parametrize(
    ("text", "answer"),
    [
        (
            "On Sat, 7 Nov 2026 at 10:00, AECA <aeca@gmail.com> wrote:\n> Hello,\n\nAPPROVE",
            Answer.APPROVE,
        ),
        ("AECA <aeca@gmail.com> wrote:\n>\n> | Approve this today.\n\nReject.", Answer.REJECT),
        (
            "On Sat, Nov 7, 2026 at 10:00 AM AECA <aeca@gmail.com>\nwrote:\n\n> Hello,\n\nApproved",
            Answer.APPROVE,
        ),
        ("Le sam. 7 nov. 2026, AECA a wrote:\n> Hello\nREJECTED", Answer.REJECT),
    ],
    ids=["on-date-name-wrote", "name-wrote", "gmail-wrapped", "ends-in-wrote"],
)
def test_a_bottom_posted_answer_under_an_attribution_line_is_read(
    text: str, answer: Answer
) -> None:
    assert parse_answer(text) is answer


@pytest.mark.parametrize(
    "text",
    [
        "Peter wrote:\nAPPROVE",
        "On second thought\nAPPROVE",
        "On Monday we meet\nAPPROVE\n> quoted",
    ],
    ids=["wrote-without-a-quote", "on-without-wrote", "on-line-not-an-attribution"],
)
def test_a_line_is_skipped_only_as_an_attribution_directly_above_a_quote(text: str) -> None:
    assert parse_answer(text) is Answer.UNCLEAR


# Fix round 2: an answer word is never skipped, and an inline reply is unclear.

REJECT_ABOVE_A_QUOTE_THEN_APPROVED = (
    "Reject \u2014 as the client wrote:\n> Tuesday at 3pm?\n\n"
    "Approved only if they move it to Wednesday.\n"
)
INLINE_PER_STEP = (
    "On Sat, Nov 7, 2026 at 10:00 AM AECA <aeca@gmail.com> wrote:\n"
    "> If you approve, AECA will:\n"
    '> 1. create the calendar event "Sync" on Tuesday 10 November 2026, 15:00\u201315:30\n'
    "Approve this one.\n"
    "> 2. send this reply:\n"
    "> | Hello, confirmed for Tuesday.\n"
    "Reject: do not send that text.\n"
)


def test_a_line_ending_in_wrote_that_starts_with_an_answer_word_is_the_answer() -> None:
    assert parse_answer("Reject \u2014 as the client wrote:\n> Tuesday at 3pm?\n") is Answer.REJECT


# The re-review's probe: its REJECT is now read as the answer line, and the APPROVED written below
# the quote makes it an inline reply, so it is unclear: the manager's intent is conditional.
def test_a_reject_followed_below_the_quote_by_approved_is_unclear() -> None:
    assert parse_answer(REJECT_ABOVE_A_QUOTE_THEN_APPROVED) is Answer.UNCLEAR


@pytest.mark.parametrize(
    "text",
    [
        INLINE_PER_STEP,
        "Approve this one.\n> 2. send this reply:\nNo, do not send that.",
        "On reflection, REJECT\nsee what Ana wrote:\n> the request\nApproved items stay",
        "On Sat, AECA wrote:\n> To answer, reply with\nAPPROVE or REJECT as the first word\n"
        "> above any quoted text.\nReject.",
    ],
    ids=[
        "inline-per-step",
        "inline-without-attribution",
        "answer-word-in-attribution",
        "rewrapped-quote-line",
    ],
)
def test_an_inline_reply_or_an_answer_word_in_an_attribution_is_unclear(text: str) -> None:
    assert parse_answer(text) is Answer.UNCLEAR


@pytest.mark.parametrize(
    "text",
    [
        "APPROVE\n\nOn Sat, Nov 7, 2026 at 10:00 AM AECA <aeca@gmail.com>\nwrote:\n\n> Hello,\n",
        "APPROVE\n\nSent with Proton Mail secure email.\n\nOn Saturday, AECA wrote:\n\n> Hello,\n",
        "On Sat, AECA <aeca@gmail.com> wrote:\n> Hello,\n> x\n\nApprove.\n\n-- \nIonel\n",
    ],
    ids=["gmail-top-posted", "proton-top-posted", "bottom-posted-with-signature"],
)
def test_an_ordinary_top_or_bottom_posted_answer_is_still_read(text: str) -> None:
    assert parse_answer(text) is Answer.APPROVE
