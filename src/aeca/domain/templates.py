"""The words AECA sends to a sender. Code writes all of them, except the urgent draft, which the
manager reads first. No line starts with APPROVE or REJECT, so a quoted line can never pass for
an answer to an approval request."""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from zoneinfo import ZoneInfo

from aeca.domain.facts import EmailFacts
from aeca.domain.outgoing import OutgoingMessage
from aeca.domain.time_range import Interval

EN_DASH = "\u2013"
SIGN_OFF = "Kind regards,\nAECA, the assistant of this mailbox"
WHITESPACE_RUN = re.compile(r"\s+")
REPLY_PREFIX = re.compile(r"re:", re.IGNORECASE)
ANSWER_WORD_AT_LINE_START = re.compile(r"^(\s*)(?=(?:APPROVE|REJECT))", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class ReplyRequest:
    facts: EmailFacts
    aeca_id: str


def reply_subject(subject: str) -> str:
    one_line = WHITESPACE_RUN.sub(" ", subject).strip()
    return one_line if REPLY_PREFIX.match(one_line) else f"Re: {one_line}"


def acknowledgement(request: ReplyRequest) -> OutgoingMessage:
    return _reply(
        request,
        kind="acknowledgement",
        paragraphs=(
            "Thank you for your email. It has reached the mailbox and has been noted.",
            "If it needs a personal answer, it will get one.",
        ),
    )


def thank_you(request: ReplyRequest) -> OutgoingMessage:
    return _reply(
        request,
        kind="thank_you",
        paragraphs=("Thank you for the information. It has been received and noted.",),
    )


def decline(request: ReplyRequest) -> OutgoingMessage:
    return _reply(
        request,
        kind="decline",
        paragraphs=(
            "Thank you for your offer. We are not interested at the moment, "
            "and we will not take it further.",
            "Please do not send further proposals to this address.",
        ),
    )


def confirmation(request: ReplyRequest, *, slot: Interval, zone: ZoneInfo) -> OutgoingMessage:
    return _reply(
        request,
        kind="confirmation",
        paragraphs=(
            "Thank you for your request. The meeting is confirmed for:",
            f"  {describe_slot(slot, zone)}",
        ),
    )


def alternatives(
    request: ReplyRequest, *, slots: Sequence[Interval], zone: ZoneInfo
) -> OutgoingMessage:
    listed = "\n".join(f"  - {describe_slot(slot, zone)}" for slot in slots)
    return _reply(
        request,
        kind="alternatives",
        paragraphs=(
            "Thank you for your request. The times you proposed do not fit the calendar. "
            "These times are free:",
            listed,
            "Please reply with the one that suits you.",
        ),
    )


def urgent_reply(request: ReplyRequest, *, draft: str) -> OutgoingMessage:
    return _reply(request, kind="urgent_reply", paragraphs=(draft.strip(),))


def describe_slot(slot: Interval, zone: ZoneInfo) -> str:
    start, end = slot.start.astimezone(zone), slot.end.astimezone(zone)
    day = f"{start:%A} {start.day} {start:%B %Y}"
    return f"{day}, {start:%H:%M}{EN_DASH}{end:%H:%M} ({zone.key})"


def _reply(request: ReplyRequest, *, kind: str, paragraphs: Sequence[str]) -> OutgoingMessage:
    facts = request.facts
    body = "\n\n".join((_greeting(facts), *paragraphs, SIGN_OFF))
    return OutgoingMessage(
        to=facts.sender,
        subject=reply_subject(facts.subject),
        body=_without_answer_words_at_line_start(body),
        kind=kind,
        aeca_id=request.aeca_id,
        thread_id=facts.thread_id,
        in_reply_to=facts.rfc822_message_id,
        references=_references(facts),
    )


def _greeting(facts: EmailFacts) -> str:
    name = WHITESPACE_RUN.sub(" ", facts.sender_name).strip()
    return f"Hello {name}," if name else "Hello,"


def _references(facts: EmailFacts) -> str | None:
    known = [value for value in (facts.references, facts.rfc822_message_id) if value]
    return " ".join(known) if known else None


def _without_answer_words_at_line_start(body: str) -> str:
    # splitlines, not split("\n"): the other line separators start a line in some readers too.
    return "".join(
        ANSWER_WORD_AT_LINE_START.sub(r"\1| ", line) for line in body.splitlines(keepends=True)
    )
