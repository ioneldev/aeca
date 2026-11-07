"""The words AECA sends to a sender, and what it sends the manager: the urgent notice, the approval
request, the help reply and the alert. Code writes all of them, except the urgent draft, which the
manager reads first. No line starts with APPROVE or REJECT, so a quoted line can never pass for an
answer to an approval request."""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from zoneinfo import ZoneInfo

from aeca.domain.facts import EmailFacts
from aeca.domain.labels import ReplyKind
from aeca.domain.outgoing import OutgoingMessage
from aeca.domain.time_range import Interval

EN_DASH = "\u2013"
SIGN_OFF = "Kind regards,\nAECA, the assistant of this mailbox"
WHITESPACE_RUN = re.compile(r"\s+")
REPLY_PREFIX = re.compile(r"re:", re.IGNORECASE)
ANSWER_WORD_AT_LINE_START = re.compile(r"^(\s*)(?=(?:APPROVE|REJECT))", re.IGNORECASE)
URGENT_NOTICE_KIND = "urgent-notice"
NOTICE_SUBJECT_PREFIX = "[AECA] Urgent: "
# The first message of an approval thread carries this kind (spec §4.5).
APPROVAL_REQUEST_KIND = "approval-request"
APPROVAL_HELP_KIND = "approval-help"
ALERT_KIND = "alert"
REQUEST_SUBJECT_PREFIX = "[AECA] Approve: "
ALERT_SUBJECT_PREFIX = "[AECA] Alert: "
HOW_TO_ANSWER = "To answer, reply with APPROVE or REJECT as the first word, above any quoted text."
# Quoted text in a request: the reply AECA would send, which the manager reads before it goes.
DRAFT_LINE_PREFIX = "| "
OUTSIDE_WORK_HOURS_NOTE = (
    "Note: this time is outside working hours. It is offered because the sender proposed it."
)
URGENT_NOTE = (
    "This email looks urgent and needs your attention soon. It is starred and labelled URGENT, "
    "and the reply drafted to the sender is below."
)
REPLY_HEADLINES = {
    "acknowledgement": "an acknowledgement",
    "thank_you": "a thank-you",
    "decline": "a polite decline",
    "confirmation": "a confirmation",
    "alternatives": "a reply proposing free times",
    "urgent_reply": "the drafted urgent reply",
}
# The reply kinds AECA sends without approval: one of them in a thread uses up the thread's
# unapproved reply (spec §4.3). Sent after an approval, they carry APPROVED_PREFIX, so they don't.
AUTOMATIC_REPLY_KINDS = frozenset(kind.value for kind in ReplyKind if kind is not ReplyKind.NONE)
APPROVED_PREFIX = "approved-"


@dataclass(frozen=True, slots=True)
class ReplyRequest:
    facts: EmailFacts
    aeca_id: str


@dataclass(frozen=True, slots=True)
class UrgentNotice:
    facts: EmailFacts
    aeca_id: str
    manager_address: str
    summary: str
    quote: str


@dataclass(frozen=True, slots=True)
class RequestStep:
    """One action of an approval in plain words, with the message it would send, if any."""

    description: str
    draft: str | None = None


@dataclass(frozen=True, slots=True)
class ApprovalRequest:
    facts: EmailFacts
    approval_id: str
    manager_address: str
    summary: str
    headline: str
    steps: tuple[RequestStep, ...]
    outside_work_hours: bool
    urgent: bool


@dataclass(frozen=True, slots=True)
class AlertNotice:
    manager_address: str
    aeca_id: str
    subject: str
    text: str


@dataclass(frozen=True, slots=True)
class EventDetails:
    facts: EmailFacts
    slot: Interval
    zone: ZoneInfo
    summary: str
    approval_id: str


def reply_subject(subject: str) -> str:
    one_line = _one_line(subject)
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


def urgent_notice(notice: UrgentNotice) -> OutgoingMessage:
    """Tells the manager about an urgent email that has no draft to approve, in a thread of its
    own: it answers nobody, so it carries no In-Reply-To."""
    facts = notice.facts
    subject = _one_line(facts.subject)
    details = "\n".join(
        (
            f"From: {_sender_line(facts)}",
            f"Subject: {subject}",
            f"Summary: {_one_line(notice.summary)}",
            f'Why it is urgent: "{_one_line(notice.quote)}"',
        )
    )
    body = "\n\n".join(
        (
            "Hello,",
            "This email looks urgent and needs your attention soon:",
            details,
            "It is starred and labelled URGENT. No reply has been drafted to the sender.",
            SIGN_OFF,
        )
    )
    return OutgoingMessage(
        to=notice.manager_address,
        subject=f"{NOTICE_SUBJECT_PREFIX}{subject}",
        body=_without_answer_words_at_line_start(body),
        kind=URGENT_NOTICE_KIND,
        aeca_id=notice.aeca_id,
        thread_id=None,
        in_reply_to=None,
        references=None,
    )


def approval_request(request: ApprovalRequest) -> OutgoingMessage:
    """Asks the manager to approve the steps, in a thread of its own (spec §4.5)."""
    notes = (
        *((OUTSIDE_WORK_HOURS_NOTE,) if request.outside_work_hours else ()),
        *((URGENT_NOTE,) if request.urgent else ()),
    )
    body = "\n\n".join(
        (
            "Hello,",
            "AECA asks for your approval before it acts on this email:",
            _source_details(request.facts, request.summary),
            "If you approve, AECA will:",
            "\n\n".join(_numbered(request.steps)),
            *notes,
            HOW_TO_ANSWER,
            SIGN_OFF,
        )
    )
    return OutgoingMessage(
        to=request.manager_address,
        subject=f"{REQUEST_SUBJECT_PREFIX}{_one_line(request.headline)}",
        body=_without_answer_words_at_line_start(body),
        kind=APPROVAL_REQUEST_KIND,
        aeca_id=request.approval_id,
        thread_id=None,
        in_reply_to=None,
        references=None,
    )


def help_reply(request: ReplyRequest) -> OutgoingMessage:
    """Answers, once, a reply to a request that holds neither APPROVE nor REJECT."""
    return _reply(
        request,
        kind=APPROVAL_HELP_KIND,
        paragraphs=(
            "AECA could not read an answer in your reply, so nothing has been done yet.",
            HOW_TO_ANSWER,
        ),
    )


def alert(notice: AlertNotice) -> OutgoingMessage:
    body = "\n\n".join(("Hello,", notice.text.strip(), SIGN_OFF))
    return OutgoingMessage(
        to=notice.manager_address,
        subject=f"{ALERT_SUBJECT_PREFIX}{_one_line(notice.subject)}",
        body=_without_answer_words_at_line_start(body),
        kind=ALERT_KIND,
        aeca_id=notice.aeca_id,
        thread_id=None,
        in_reply_to=None,
        references=None,
    )


def event_description(details: EventDetails) -> str:
    """What the calendar event says about where it came from (spec §4.4)."""
    facts = details.facts
    text = "\n".join(
        (
            f"Requested by: {_sender_line(facts)}",
            f"Subject: {_one_line(facts.subject)}",
            f"Summary: {_one_line(details.summary)}",
            f"Time: {describe_slot(details.slot, details.zone)}",
            f"Approval: {details.approval_id}",
            "Created by AECA after the manager approved it.",
        )
    )
    return _without_answer_words_at_line_start(text)


def event_step(title: str, slot: Interval, zone: ZoneInfo) -> str:
    return f'create the calendar event "{_one_line(title)}" on {describe_slot(slot, zone)}'


def event_headline(title: str, slot: Interval, zone: ZoneInfo) -> str:
    return f'meeting "{_one_line(title)}", {describe_slot(slot, zone)}'


def reply_step(facts: EmailFacts) -> str:
    return f"send {_sender_line(facts)} this reply:"


def reply_headline(template: str, facts: EmailFacts) -> str:
    return f"{REPLY_HEADLINES.get(template, 'a reply')} to {_sender_line(facts)}"


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


def _source_details(facts: EmailFacts, summary: str) -> str:
    return "\n".join(
        (
            f"From: {_sender_line(facts)}",
            f"Subject: {_one_line(facts.subject)}",
            f"Summary: {_one_line(summary)}",
        )
    )


def _numbered(steps: Sequence[RequestStep]) -> list[str]:
    return [
        f"{number}. {step.description}" + (f"\n{_quoted(step.draft)}" if step.draft else "")
        for number, step in enumerate(steps, start=1)
    ]


def _quoted(draft: str) -> str:
    return "\n".join((DRAFT_LINE_PREFIX + line).rstrip() for line in draft.splitlines())


def _sender_line(facts: EmailFacts) -> str:
    name = _one_line(facts.sender_name)
    return f"{name} <{facts.sender}>" if name else facts.sender


def _one_line(text: str) -> str:
    return WHITESPACE_RUN.sub(" ", text).strip()


def _greeting(facts: EmailFacts) -> str:
    name = _one_line(facts.sender_name)
    return f"Hello {name}," if name else "Hello,"


def _references(facts: EmailFacts) -> str | None:
    known = [value for value in (facts.references, facts.rfc822_message_id) if value]
    return " ".join(known) if known else None


def _without_answer_words_at_line_start(body: str) -> str:
    # splitlines, not split("\n"): the other line separators start a line in some readers too.
    return "".join(
        ANSWER_WORD_AT_LINE_START.sub(r"\1| ", line) for line in body.splitlines(keepends=True)
    )
