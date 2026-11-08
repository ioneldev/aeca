"""R4, the hand reply in a rehearsal approval thread, is decided by the message alone: its own
In-Reply-To or References must name the Message-ID of a rehearsal approval request. The thread it
sits in never decides (fix round 1, I1)."""

from datetime import UTC, datetime, timedelta

from support.emails import make_raw

from aeca.domain.email import RawEmail
from aeca.domain.ids import approval_id
from aeca.domain.rehearsal_mail import LeftoverRule, mail_item_of, rehearsal_mail

ACCOUNT = "aecatest74@gmail.com"
S_EXT = "ionel.cimpan@protonmail.com"
GRADER = "grader@telekom.example"
SENDERS = frozenset({S_EXT, "ionel.cimpan@icloud.com"})
REQUEST_ID = "<request@mail.gmail.com>"
START = datetime(2026, 11, 7, 9, 0, tzinfo=UTC)
THREAD = "t-request"


def source() -> RawEmail:
    return make_raw(
        sender=S_EXT,
        to=ACCOUNT,
        subject="Planning call",
        authentication_results=[
            "mx.google.com; dmarc=pass (p=QUARANTINE) header.from=protonmail.com"
        ],
        message_id="src",
        thread_id="t-source",
        internal_date=START,
    )


def request(*, to: str = ACCOUNT) -> RawEmail:
    return make_raw(
        sender=ACCOUNT,
        to=to,
        subject="[AECA] Approve: Planning call",
        headers={
            "Message-ID": REQUEST_ID,
            "X-AECA-Kind": "approval-request",
            "X-AECA-Id": approval_id("src", 1),
        },
        label_ids=("SENT",),
        message_id="request",
        thread_id=THREAD,
        internal_date=START + timedelta(minutes=1),
    )


def reply(
    message_id: str,
    *,
    headers: dict[str, str] | None = None,
    sender: str = ACCOUNT,
    to: str = ACCOUNT,
    label_ids: tuple[str, ...] = ("SENT", "INBOX"),
    thread_id: str = THREAD,
) -> RawEmail:
    return make_raw(
        sender=sender,
        to=to,
        subject="Re: [AECA] Approve: Planning call",
        body="APPROVE",
        headers=headers,
        label_ids=label_ids,
        message_id=message_id,
        thread_id=thread_id,
        internal_date=START + timedelta(minutes=5),
    )


def rules_of(*raws: RawEmail) -> dict[str, LeftoverRule]:
    items = [mail_item_of(raw, ACCOUNT) for raw in raws]
    return {match.item.message_id: match.rule for match in rehearsal_mail(items, SENDERS)}


def test_a_hand_reply_whose_in_reply_to_names_a_rehearsal_request_matches_r4() -> None:
    rules = rules_of(source(), request(), reply("hand", headers={"In-Reply-To": REQUEST_ID}))

    assert rules["hand"] is LeftoverRule.REPLY_TO_A_REHEARSAL_APPROVAL_REQUEST


def test_a_hand_reply_found_through_references_alone_matches_r4() -> None:
    references = f"<earlier@mail.gmail.com> {REQUEST_ID}"
    rules = rules_of(source(), request(), reply("hand", headers={"References": references}))

    assert rules["hand"] is LeftoverRule.REPLY_TO_A_REHEARSAL_APPROVAL_REQUEST


def test_a_sent_message_in_the_requests_thread_that_names_nothing_is_left_alone() -> None:
    rules = rules_of(source(), request(), reply("same-thread"))

    assert "same-thread" not in rules


def test_a_reply_naming_another_message_is_left_alone_even_in_the_requests_thread() -> None:
    headers = {"In-Reply-To": "<grader-mail@telekom.example>"}

    rules = rules_of(source(), request(), reply("elsewhere", to=GRADER, headers=headers))

    assert "elsewhere" not in rules


def test_mail_that_aeca_wrote_is_never_matched_by_r4() -> None:
    their_request = {
        "In-Reply-To": REQUEST_ID,
        "X-AECA-Kind": "approval-request",
        "X-AECA-Id": approval_id("their-email", 0),
    }

    rules = rules_of(source(), request(), reply("theirs", headers=their_request))

    assert "theirs" not in rules


def test_only_an_exact_message_id_counts() -> None:
    near = {"In-Reply-To": "<request@mail.gmail.com.evil.example>"}

    rules = rules_of(source(), request(), reply("near", headers=near))

    assert "near" not in rules


def test_an_inbound_reply_naming_the_request_needs_another_rule() -> None:
    inbound = {"In-Reply-To": REQUEST_ID}

    rules = rules_of(
        source(), request(), reply("inbound", sender=GRADER, label_ids=("INBOX",), headers=inbound)
    )

    assert "inbound" not in rules


def test_a_reply_to_aecas_other_mail_to_a_rehearsal_sender_is_left_alone() -> None:
    acknowledgement = make_raw(
        sender=ACCOUNT,
        to=S_EXT,
        subject="Re: Planning call",
        headers={
            "Message-ID": "<ack@mail.gmail.com>",
            "X-AECA-Kind": "acknowledgement",
            "X-AECA-Id": "k-0123456789abcdef",
        },
        label_ids=("SENT",),
        message_id="ack",
        thread_id="t-source",
        internal_date=START + timedelta(minutes=2),
    )
    headers = {"In-Reply-To": "<ack@mail.gmail.com>"}

    rules = rules_of(
        source(), acknowledgement, reply("to-ack", thread_id="t-source", headers=headers)
    )

    assert rules["ack"] is LeftoverRule.SENT_ONLY_TO_REHEARSAL_SENDERS
    assert "to-ack" not in rules
