"""The labels an email can get, what each one means to the model, and which replies it allows."""

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

URGENT_LABEL = "URGENT"
FINANCE_LABEL = "Finance"
PROCESSED = "AECA/processed"
ERROR = "AECA/error"
AWAITING = "AECA/awaiting"


class ReplyKind(StrEnum):
    NONE = "none"
    ACKNOWLEDGEMENT = "acknowledgement"
    THANK_YOU = "thank_you"
    DECLINE = "decline"


class Label(StrEnum):
    MEETING_REQUEST = "MEETING_REQUEST"
    TASK = "TASK"
    INFO_ONLY = "INFO_ONLY"
    SALES_OUTREACH = "SALES_OUTREACH"
    MARKETING = "MARKETING"
    SPAM = "SPAM"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class LabelRecord:
    label: Label
    description: str
    examples: tuple[str, ...]
    allowed_replies: frozenset[ReplyKind]


NO_REPLIES: frozenset[ReplyKind] = frozenset()

LABEL_RECORDS: Mapping[Label, LabelRecord] = {
    Label.MEETING_REQUEST: LabelRecord(
        label=Label.MEETING_REQUEST,
        description=(
            "Someone asks to meet, call or schedule a time, or proposes one. "
            "Extract every proposed time as a window, each with the exact text it came from."
        ),
        examples=(
            "Can we talk on Tuesday at 3pm?",
            "Are you free for a 30 minute call next week?",
            "Let's set up a demo. Thursday morning or Friday after lunch both work for me.",
        ),
        allowed_replies=NO_REPLIES,
    ),
    Label.TASK: LabelRecord(
        label=Label.TASK,
        description=(
            "The sender asks the manager, or the team, to do something: review, send, decide, "
            "fix or prepare a thing."
        ),
        examples=(
            "Please review the attached contract and send me your comments.",
            "Could you prepare the Q3 numbers before the board call?",
            "I need you to approve the purchase order.",
        ),
        allowed_replies=frozenset({ReplyKind.ACKNOWLEDGEMENT}),
    ),
    Label.INFO_ONLY: LabelRecord(
        label=Label.INFO_ONLY,
        description=(
            "Information that needs no action: an update, a confirmation, a notice, an invoice "
            "or a receipt. Set finance for an invoice or a receipt."
        ),
        examples=(
            "FYI: the office is closed on Monday.",
            "Your invoice 2026-114 is attached.",
            "Receipt for your order #8812.",
        ),
        allowed_replies=frozenset({ReplyKind.THANK_YOU}),
    ),
    Label.SALES_OUTREACH: LabelRecord(
        label=Label.SALES_OUTREACH,
        description=(
            "An unsolicited offer of a product or service, written to win the manager as a "
            "customer, often a cold email asking for a call."
        ),
        examples=(
            "Hi, I help companies like yours cut cloud costs by 30%. Do you have 15 minutes?",
            "Our team can take over your recruiting. May I send a proposal?",
            "Following up on my last email about our CRM.",
        ),
        allowed_replies=frozenset({ReplyKind.DECLINE}),
    ),
    Label.MARKETING: LabelRecord(
        label=Label.MARKETING,
        description=(
            "A newsletter, promotion or other bulk mail sent to many people, not written to the "
            "manager in person."
        ),
        examples=(
            "Our autumn sale: 40% off everything.",
            "This week's product newsletter.",
            "You're invited to our annual webinar.",
        ),
        allowed_replies=frozenset({ReplyKind.ACKNOWLEDGEMENT}),
    ),
    Label.SPAM: LabelRecord(
        label=Label.SPAM,
        description=(
            "Phishing, scams and junk: a fake login or invoice link, a prize or inheritance, "
            "a threat, a sender who pretends to be someone they aren't."
        ),
        examples=(
            "Your account is locked. Verify your password at the link below.",
            "You won 1,000,000 euros. Send your bank details to claim it.",
            "Urgent wire transfer needed, reply with your gift card codes.",
        ),
        allowed_replies=NO_REPLIES,
    ),
    Label.UNKNOWN: LabelRecord(
        label=Label.UNKNOWN,
        description="Use only when no other label fits.",
        examples=(
            "An email with no readable text.",
            "A message too mixed or too short to tell what it wants.",
        ),
        allowed_replies=NO_REPLIES,
    ),
}

URGENCY_DEFINITION = """\
Urgent means the manager must act soon, or something real is lost.

Urgent:
- a deadline today, or within the next two working days;
- an outage or an incident;
- an escalation, such as "escalating this to you", a customer threatening to leave, or a second \
chase that got no answer.

Not urgent:
- urgency on the sender's side, such as "Sale ends tonight!" or "Offer expires Friday";
- a deadline weeks away;
- "URGENT" in a subject that nothing in the body supports.

Evidence: when you mark an email urgent, quote the exact text that makes it urgent, word for word. \
The Importance and X-Priority headers are a hint only, because marketers set them too. \
Never mark an email urgent without a quote."""
