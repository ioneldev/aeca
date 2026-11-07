"""The manager's answer to an approval request: which reply counts, and what it says.

Code reads it, never the model (spec §4.5). A reply counts only when it provably comes from the
manager; its answer is the first word of its first line not quoted with `>`.
"""

import unicodedata
from dataclasses import dataclass
from enum import StrEnum

from aeca.domain.email import RawEmail, body_text, parse_message
from aeca.domain.facts import EmailFacts, same_gmail_account
from aeca.domain.text import html_to_text


class Answer(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"
    UNCLEAR = "unclear"


@dataclass(frozen=True, slots=True)
class AnswerRules:
    account_address: str
    # MANAGER_ADDRESS as set, or None when it is empty: then only the account itself can answer.
    manager_address: str | None


CARRIES_AECA_HEADERS = "carries_aeca_headers"
ACCOUNT_WITHOUT_SENT = "from_the_account_without_sent"
NOT_SENT_BY_THE_ACCOUNT = "not_sent_by_the_account"
SENDER_AMBIGUOUS = "sender_ambiguous"
NOT_FROM_THE_MANAGER = "not_from_the_manager"
MANAGER_DMARC_NOT_PASS = "manager_dmarc_not_pass"
MANAGER_DMARC_FOR_ANOTHER_DOMAIN = "manager_dmarc_for_another_domain"

ANSWER_WORDS = {
    "approve": Answer.APPROVE,
    "approved": Answer.APPROVE,
    "reject": Answer.REJECT,
    "rejected": Answer.REJECT,
}
# Invisible characters a mail client may put before the first word; str.isspace() misses them.
ZERO_WIDTH = frozenset("​‌‍⁠﻿")
QUOTE_MARK = ">"


def parse_answer(text: str) -> Answer:
    line = _first_unquoted_line(text)
    if line is None:
        return Answer.UNCLEAR
    return ANSWER_WORDS.get(_first_word(line).casefold(), Answer.UNCLEAR)


def answer_of(raw: RawEmail) -> Answer:
    """Reads the email itself rather than its facts, which escape invisible characters the
    manager's client may put before the answer."""
    plain, html = body_text(parse_message(raw))
    text = plain if plain and plain.strip() else html_to_text(html or "")
    return parse_answer(text)


def counts_as_answer(message: EmailFacts, rules: AnswerRules) -> bool:
    """The caller has already checked that the message is in the request's thread."""
    return why_not_an_answer(message, rules) is None


def why_not_an_answer(message: EmailFacts, rules: AnswerRules) -> str | None:
    if message.aeca_kind is not None or message.aeca_id is not None:
        return CARRIES_AECA_HEADERS
    # SENT is a label only the account can give, and whoever holds the account is the manager.
    if message.sent_by_account:
        return None
    # The account answers only through SENT, so a From that merely claims to be the account never
    # counts, whatever its DMARC result and even when MANAGER_ADDRESS names the account.
    if same_gmail_account(message.sender, rules.account_address):
        return ACCOUNT_WITHOUT_SENT
    if rules.manager_address is None:
        return NOT_SENT_BY_THE_ACCOUNT
    return _why_not_the_manager(message, rules.manager_address)


# A DMARC pass proves only the From domain Gmail judged it for, so it must be the domain of the
# one sender AECA reads: a second From address or header could otherwise carry the pass.
def _why_not_the_manager(message: EmailFacts, manager_address: str) -> str | None:
    if not message.single_sender:
        return SENDER_AMBIGUOUS
    if not same_gmail_account(message.sender, manager_address):
        return NOT_FROM_THE_MANAGER
    if message.auth.dmarc != "pass":
        return MANAGER_DMARC_NOT_PASS
    if message.auth.dmarc_domain != _domain_of(message.sender):
        return MANAGER_DMARC_FOR_ANOTHER_DOMAIN
    return None


def _domain_of(address: str) -> str:
    return address.strip().lower().rpartition("@")[2]


def _first_unquoted_line(text: str) -> str | None:
    for line in text.splitlines():
        content = _without_leading_blanks(line)
        if content and not content.startswith(QUOTE_MARK):
            return content
    return None


def _without_leading_blanks(line: str) -> str:
    start = 0
    while start < len(line) and (line[start].isspace() or line[start] in ZERO_WIDTH):
        start += 1
    return line[start:]


# Trailing punctuation is dropped ("Approve." and "APPROVE!"); leading punctuation is not, so a
# line AECA prefixed with "| " can never pass for an answer.
def _first_word(line: str) -> str:
    word = line.split(maxsplit=1)[0]
    while word and unicodedata.category(word[-1]).startswith("P"):
        word = word[:-1]
    return word if word.isalpha() else ""
