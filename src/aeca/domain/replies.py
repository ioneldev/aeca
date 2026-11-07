"""When AECA may reply to a sender, and what an urgent draft may contain."""

import re
from dataclasses import dataclass

from aeca.domain.facts import EmailFacts, same_gmail_account
from aeca.domain.labels import Label
from aeca.domain.text import visible

URGENT_DRAFT_LIMIT = 800

SPAM_SENDER = "the email is spam"
OWN_ACCOUNT = "the sender is the account itself"
DMARC_FAILED = "the sender's DMARC check failed"
AUTOMATED_SENDER = "the sender is automated"
THREAD_ALREADY_ANSWERED = "the thread already has a reply sent without approval"

# An approved reply is still refused for these, because nobody should be written to on a
# forged, self-sent or spam email even with a manager's click behind it.
REFUSED_EVEN_WITH_APPROVAL = frozenset({SPAM_SENDER, OWN_ACCOUNT, DMARC_FAILED})

WELL_KNOWN_TLDS = (
    "com|org|net|edu|gov|io|info|biz|xyz|top|app|dev|ru|cn|eu|de|uk|ro|online|site|shop|link|click"
)
LINK_IN_TEXT = re.compile(
    rf"https?://|www\.|(?<![\w@.-])[a-z0-9-]+(?:\.[a-z0-9-]+)*\.(?:{WELL_KNOWN_TLDS})(?![a-z0-9-])"
    r"|(?<![\w@.-])[a-z0-9-]+(?:\.[a-z0-9-]+)+/\S*",
    re.IGNORECASE,
)
ADDRESS_IN_TEXT = re.compile(r"\S+@\S+\.\S+")


@dataclass(frozen=True, slots=True)
class ReplyContext:
    label: Label
    account_address: str
    thread_has_unapproved_reply: bool


def reply_block_reason(facts: EmailFacts, context: ReplyContext) -> str | None:
    if context.label is Label.SPAM:
        return SPAM_SENDER
    if same_gmail_account(facts.sender, context.account_address):
        return OWN_ACCOUNT
    if facts.auth.dmarc == "fail":
        return DMARC_FAILED
    if facts.automated:
        return AUTOMATED_SENDER
    if context.thread_has_unapproved_reply:
        return THREAD_ALREADY_ANSWERED
    return None


def check_urgent_draft(text: str) -> str | None:
    """The reason to drop the draft, or None. The model wrote it after reading one email, so
    whatever it holds that a sender could have planted, a link or an address, is refused."""
    shown = visible(text)
    if LINK_IN_TEXT.search(shown):
        return "contains a link"
    if ADDRESS_IN_TEXT.search(shown):
        return "contains an email address"
    if len(shown) > URGENT_DRAFT_LIMIT:
        return f"is longer than {URGENT_DRAFT_LIMIT} characters"
    return None
