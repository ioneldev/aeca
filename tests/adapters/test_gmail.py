import base64
from datetime import UTC, datetime
from email import message_from_bytes, policy
from email.message import EmailMessage
from typing import Any

import pytest
from fakes.google_stub import GoogleStub, http_error

from aeca.adapters.gmail import INBOX_QUERY, MAX_INBOX_IDS_PER_TICK, GmailMailbox
from aeca.domain.errors import EmailError, SystemWideError
from aeca.domain.outgoing import OutgoingMessage

ACCOUNT = "aeca.test@gmail.com"


def mailbox_with(stub: GoogleStub | None = None) -> tuple[GmailMailbox, GoogleStub]:
    stub = stub or GoogleStub()
    return GmailMailbox(stub, ACCOUNT), stub


def a_reply(**overrides: Any) -> OutgoingMessage:
    fields: dict[str, Any] = {
        "to": "Jürgen Müller <jurgen@example.de>",
        "subject": "Re: Änderung",
        "body": "Vielen Dank für Ihre Nachricht.\nGrüße",
        "kind": "decline",
        "aeca_id": "id-123",
        "thread_id": "thread-9",
        "in_reply_to": "<orig@mail.example.de>",
        "references": "<first@mail.example.de> <orig@mail.example.de>",
    }
    return OutgoingMessage(**(fields | overrides))


def drafted_message(stub: GoogleStub) -> EmailMessage:
    (created,) = stub.calls_to("users.drafts.create")
    raw = created["body"]["message"]["raw"]
    parsed = message_from_bytes(base64.urlsafe_b64decode(raw), policy=policy.default)
    assert isinstance(parsed, EmailMessage)
    return parsed


def create_draft_for(message: OutgoingMessage) -> tuple[EmailMessage, GoogleStub]:
    mailbox, stub = mailbox_with()
    stub.reply("users.drafts.create", {"id": "draft-1"})
    assert mailbox.create_draft(message) == "draft-1"
    return drafted_message(stub), stub


def test_a_reply_carries_threading_and_aeca_headers() -> None:
    parsed, stub = create_draft_for(a_reply())

    assert parsed["From"] == ACCOUNT
    assert parsed["In-Reply-To"] == "<orig@mail.example.de>"
    assert parsed["References"] == "<first@mail.example.de> <orig@mail.example.de>"
    assert parsed["Auto-Submitted"] == "auto-replied"
    assert parsed["X-AECA-Kind"] == "decline"
    assert parsed["X-AECA-Id"] == "id-123"
    (created,) = stub.calls_to("users.drafts.create")
    assert created["body"]["message"]["threadId"] == "thread-9"
    assert created["userId"] == "me"


def test_a_message_with_no_parent_is_auto_generated_and_starts_a_thread() -> None:
    request = a_reply(thread_id=None, in_reply_to=None, references=None, to="boss@example.com")

    parsed, stub = create_draft_for(request)

    assert parsed["Auto-Submitted"] == "auto-generated"
    assert parsed["In-Reply-To"] is None
    assert parsed["References"] is None
    assert parsed["X-AECA-Id"] == "id-123"
    (created,) = stub.calls_to("users.drafts.create")
    assert "threadId" not in created["body"]["message"]


def test_non_ascii_subject_and_name_are_encoded() -> None:
    parsed, _ = create_draft_for(a_reply())

    assert parsed["Subject"] == "Re: Änderung"
    (recipient,) = parsed["To"].addresses
    assert recipient.display_name == "Jürgen Müller"
    assert recipient.addr_spec == "jurgen@example.de"
    body = parsed.get_content()
    assert "Grüße" in body
    assert parsed.get_content_type() == "text/plain"
    assert parsed.get_content_charset() == "utf-8"


def test_the_wire_form_holds_only_ascii_headers() -> None:
    _, stub = create_draft_for(a_reply())
    (created,) = stub.calls_to("users.drafts.create")

    wire = base64.urlsafe_b64decode(created["body"]["message"]["raw"])
    header_block = wire.split(b"\r\n\r\n")[0]

    assert header_block.isascii()


def test_a_subject_with_a_line_break_does_not_break_the_header() -> None:
    parsed, _ = create_draft_for(a_reply(subject="Re: two\r\nlines"))

    assert parsed["Subject"] == "Re: two lines"


def test_unprocessed_inbox_is_oldest_first() -> None:
    mailbox, stub = mailbox_with()
    stub.reply("users.messages.list", {"messages": [{"id": "new"}, {"id": "mid"}, {"id": "old"}]})

    assert mailbox.unprocessed_inbox(limit=2) == ["old", "mid"]
    (asked,) = stub.calls_to("users.messages.list")
    assert asked["q"] == INBOX_QUERY == "in:inbox -label:AECA-processed"
    assert asked["userId"] == "me"
    assert asked["maxResults"] == 500


def test_unprocessed_inbox_pages_before_reversing() -> None:
    mailbox, stub = mailbox_with()
    stub.reply(
        "users.messages.list",
        {"messages": [{"id": "c"}, {"id": "b"}], "nextPageToken": "next"},
        {"messages": [{"id": "a"}]},
    )

    assert mailbox.unprocessed_inbox(limit=10) == ["a", "b", "c"]
    assert [call.get("pageToken") for call in stub.calls_to("users.messages.list")] == [
        None,
        "next",
    ]


def test_an_empty_inbox_has_no_messages_key() -> None:
    mailbox, stub = mailbox_with()
    stub.reply("users.messages.list", {"resultSizeEstimate": 0})

    assert mailbox.unprocessed_inbox(limit=5) == []


def labels_listed(stub: GoogleStub, *names: str) -> None:
    stub.reply(
        "users.labels.list",
        {"labels": [{"id": f"Label_{n}", "name": n, "type": "user"} for n in names]},
    )


def test_modify_maps_names_to_ids_and_creates_missing_labels() -> None:
    mailbox, stub = mailbox_with()
    labels_listed(stub, "TASK")
    stub.reply("users.labels.create", {"id": "Label_new", "name": "AECA/processed"})
    stub.reply("users.messages.modify", {"id": "m1"})

    mailbox.modify("m1", add=["TASK", "AECA/processed"], remove=["INBOX", "UNREAD"])

    (created,) = stub.calls_to("users.labels.create")
    assert created["body"]["name"] == "AECA/processed"
    (changed,) = stub.calls_to("users.messages.modify")
    assert changed == {
        "userId": "me",
        "id": "m1",
        "body": {"addLabelIds": ["Label_TASK", "Label_new"], "removeLabelIds": ["INBOX", "UNREAD"]},
    }


def test_the_label_cache_is_reused_between_calls() -> None:
    mailbox, stub = mailbox_with()
    labels_listed(stub, "TASK")
    stub.reply("users.messages.modify", {"id": "m1"})

    mailbox.modify("m1", add=["TASK"], remove=[])
    mailbox.modify("m2", add=["TASK"], remove=[])

    assert len(stub.calls_to("users.labels.list")) == 1


@pytest.mark.parametrize(
    "system_label", ["INBOX", "SPAM", "STARRED", "UNREAD", "TRASH", "IMPORTANT"]
)
def test_system_labels_pass_through_as_ids(system_label: str) -> None:
    mailbox, stub = mailbox_with()
    stub.reply("users.messages.modify", {"id": "m1"})

    mailbox.modify("m1", add=[system_label], remove=[])

    assert "users.labels.list" not in stub.paths_called()
    assert stub.calls_to("users.messages.modify")[0]["body"]["addLabelIds"] == [system_label]


def test_modify_targets_the_message_never_the_thread() -> None:
    mailbox, stub = mailbox_with()
    labels_listed(stub, "TASK")
    stub.reply("users.messages.modify", {"id": "m1"})

    mailbox.modify("m1", add=["TASK"], remove=["INBOX"])

    modifying = {path for path in stub.paths_called() if "modify" in path.lower()}
    assert modifying == {"users.messages.modify"}


def test_a_label_created_meanwhile_is_picked_up_after_a_conflict() -> None:
    mailbox, stub = mailbox_with()
    stub.reply("users.labels.list", {"labels": []}, {"labels": [{"id": "L9", "name": "URGENT"}]})
    stub.reply("users.labels.create", http_error(409))
    stub.reply("users.messages.modify", {"id": "m1"})

    mailbox.modify("m1", add=["URGENT"], remove=[])

    assert stub.calls_to("users.messages.modify")[0]["body"]["addLabelIds"] == ["L9"]


def test_ensure_labels_creates_only_the_missing_user_labels() -> None:
    mailbox, stub = mailbox_with()
    labels_listed(stub, "TASK")
    stub.reply("users.labels.create", {"id": "L1"})

    mailbox.ensure_labels(["TASK", "INBOX", "AECA/error", "AECA/awaiting"])

    created = [call["body"]["name"] for call in stub.calls_to("users.labels.create")]
    assert created == ["AECA/error", "AECA/awaiting"]


def test_get_decodes_the_raw_message_and_the_epoch_milliseconds() -> None:
    mailbox, stub = mailbox_with()
    rfc822 = "Subject: Änderung\r\n\r\nHi".encode()
    stub.reply(
        "users.messages.get",
        {
            "id": "m1",
            "threadId": "t1",
            "labelIds": ["INBOX", "UNREAD"],
            "internalDate": "1794225600000",
            "raw": base64.urlsafe_b64encode(rfc822).decode().rstrip("="),
        },
    )

    email = mailbox.get("m1")

    assert email.rfc822 == rfc822
    assert (email.message_id, email.thread_id) == ("m1", "t1")
    assert email.label_ids == frozenset({"INBOX", "UNREAD"})
    assert email.internal_date == datetime(2026, 11, 9, 12, 0, tzinfo=UTC)
    assert stub.calls_to("users.messages.get") == [{"userId": "me", "id": "m1", "format": "raw"}]


def test_thread_summaries_are_oldest_first_with_bare_lowercase_senders() -> None:
    mailbox, stub = mailbox_with()

    def message(message_id: str, millis: str, sender: str, **aeca: str) -> dict[str, Any]:
        headers = [{"name": "From", "value": sender}]
        headers += [{"name": name, "value": value} for name, value in aeca.items()]
        return {
            "id": message_id,
            "threadId": "t1",
            "labelIds": ["SENT"],
            "internalDate": millis,
            "payload": {"headers": headers},
        }

    stub.reply(
        "users.threads.get",
        {
            "messages": [
                message(
                    "m2",
                    "2000",
                    "AECA <Aeca.Test@Gmail.com>",
                    **{"X-AECA-Kind": "approval", "X-AECA-Id": "a1"},
                ),
                message("m1", "1000", '"Jürgen Müller" <Jurgen@Example.DE>'),
            ]
        },
    )

    first, second = mailbox.thread_summaries("t1")

    assert (first.message_id, first.sender, first.aeca_kind, first.aeca_id) == (
        "m1",
        "jurgen@example.de",
        None,
        None,
    )
    assert (second.sender, second.aeca_kind, second.aeca_id) == (
        "aeca.test@gmail.com",
        "approval",
        "a1",
    )
    assert second.label_ids == frozenset({"SENT"})
    assert first.internal_date == datetime(1970, 1, 1, 0, 0, 1, tzinfo=UTC)
    (asked,) = stub.calls_to("users.threads.get")
    assert asked["format"] == "metadata"
    assert asked["metadataHeaders"] == ["From", "X-AECA-Kind", "X-AECA-Id"]


def test_draft_exists_is_false_on_a_404_and_true_otherwise() -> None:
    mailbox, stub = mailbox_with()
    stub.reply("users.drafts.get", {"id": "d1"}, http_error(404))

    assert mailbox.draft_exists("d1") is True
    assert mailbox.draft_exists("d1") is False


def test_draft_exists_does_not_hide_other_failures() -> None:
    mailbox, stub = mailbox_with()
    stub.reply("users.drafts.get", http_error(503))

    with pytest.raises(SystemWideError):
        mailbox.draft_exists("d1")


def test_send_draft_returns_the_sent_message() -> None:
    mailbox, stub = mailbox_with()
    stub.reply("users.drafts.send", {"id": "sent-1", "threadId": "t-1"})

    sent = mailbox.send_draft("d1")

    assert (sent.message_id, sent.thread_id) == ("sent-1", "t-1")
    assert stub.calls_to("users.drafts.send") == [{"userId": "me", "body": {"id": "d1"}}]


def test_google_failures_leave_the_adapter_as_aeca_errors() -> None:
    mailbox, stub = mailbox_with()
    stub.reply("users.messages.get", http_error(404))

    with pytest.raises(EmailError):
        mailbox.get("missing")


def test_address_reads_the_profile() -> None:
    mailbox, stub = mailbox_with()
    stub.reply("users.getProfile", {"emailAddress": "real@gmail.com"})

    assert mailbox.address() == "real@gmail.com"
    assert stub.calls_to("users.getProfile") == [{"userId": "me"}]


@pytest.mark.parametrize("field", ["to", "subject", "in_reply_to", "references", "kind", "aeca_id"])
def test_a_line_break_in_any_header_value_cannot_inject_a_header(field: str) -> None:
    hostile = "<r@b.c>\r\nBcc: x@evil.example"

    parsed, _ = create_draft_for(a_reply(**{field: hostile}))

    assert parsed["Bcc"] is None
    assert len(parsed.get_all("Bcc", [])) == 0


def test_a_crafted_references_header_survives_facts_and_templates() -> None:
    from support.emails import make_raw
    from support.facts import ACCOUNT_ADDRESS

    from aeca.domain.facts import FactSettings, extract_facts
    from aeca.domain.templates import ReplyRequest, decline

    crafted = "=?utf-8?q?<r@b.c>=0D=0ABcc:_x@evil.example?="
    raw = make_raw(headers={"References": crafted, "Message-ID": "<m@b.c>"})
    settings = FactSettings(ACCOUNT_ADDRESS, frozenset(), frozenset())
    facts = extract_facts(raw, settings)
    outgoing = decline(ReplyRequest(facts=facts, aeca_id="a1"))

    parsed, _ = create_draft_for(outgoing)

    assert parsed["Bcc"] is None


def test_an_unbuildable_message_is_an_email_error_not_a_value_error() -> None:
    mailbox, _ = mailbox_with()

    with pytest.raises(EmailError):
        mailbox.create_draft(a_reply(to="not an address <<<"))


def test_a_label_differing_only_in_case_is_found_after_a_conflict() -> None:
    mailbox, stub = mailbox_with()
    stub.reply("users.labels.list", {"labels": []}, {"labels": [{"id": "L7", "name": "Urgent"}]})
    stub.reply("users.labels.create", http_error(409))
    stub.reply("users.messages.modify", {"id": "m1"})

    mailbox.modify("m1", add=["URGENT"], remove=[])

    assert stub.calls_to("users.messages.modify")[0]["body"]["addLabelIds"] == ["L7"]


def test_a_conflict_with_no_matching_label_is_an_email_error() -> None:
    mailbox, stub = mailbox_with()
    stub.reply("users.labels.list", {"labels": []})
    stub.reply("users.labels.create", http_error(409))

    with pytest.raises(EmailError):
        mailbox.modify("m1", add=["URGENT"], remove=[])


def test_the_inbox_listing_stops_at_the_cap_and_keeps_the_oldest_read() -> None:
    mailbox, stub = mailbox_with()
    counter = iter(range(10**6))

    def endless_page(_arguments: dict[str, Any]) -> dict[str, Any]:
        return {
            "messages": [{"id": f"m{next(counter)}"} for _ in range(500)],
            "nextPageToken": "more",
        }

    stub.reply("users.messages.list", endless_page)

    ids = mailbox.unprocessed_inbox(limit=3)

    assert len(stub.calls_to("users.messages.list")) == MAX_INBOX_IDS_PER_TICK // 500
    assert ids == [
        f"m{MAX_INBOX_IDS_PER_TICK - 1}",
        f"m{MAX_INBOX_IDS_PER_TICK - 2}",
        f"m{MAX_INBOX_IDS_PER_TICK - 3}",
    ]


def sent_metadata(message_id: str, aeca_id: str | None) -> dict[str, Any]:
    headers = [] if aeca_id is None else [{"name": "X-AECA-Id", "value": aeca_id}]
    return {"id": message_id, "payload": {"headers": headers}}


def test_recently_sent_reads_the_sent_label_and_stops_at_the_matching_aeca_id() -> None:
    mailbox, stub = mailbox_with()
    stub.reply("users.messages.list", {"messages": [{"id": "s3"}, {"id": "s2"}, {"id": "s1"}]})
    stub.reply(
        "users.messages.get",
        sent_metadata("s3", "k-other"),
        sent_metadata("s2", "k-1"),
        sent_metadata("s1", "k-1"),
    )

    found = mailbox.recently_sent_with_aeca_id("k-1", 25)

    assert found == "s2"
    assert stub.calls_to("users.messages.list") == [
        {"userId": "me", "labelIds": ["SENT"], "maxResults": 25}
    ]
    assert stub.calls_to("users.messages.get") == [
        {"userId": "me", "id": message_id, "format": "metadata", "metadataHeaders": ["X-AECA-Id"]}
        for message_id in ("s3", "s2")
    ]


def test_recently_sent_finds_nothing_in_an_empty_or_unmatched_sent_folder() -> None:
    mailbox, stub = mailbox_with()
    stub.reply("users.messages.list", {}, {"messages": [{"id": "s1"}]})
    stub.reply("users.messages.get", sent_metadata("s1", None))

    assert mailbox.recently_sent_with_aeca_id("k-1", 25) is None
    assert mailbox.recently_sent_with_aeca_id("k-1", 25) is None
