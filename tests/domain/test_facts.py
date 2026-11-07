from datetime import UTC, datetime

import pytest
from support.emails import make_raw

from aeca.domain.email import RawEmail
from aeca.domain.facts import (
    Attachment,
    FactSettings,
    extract_facts,
    same_gmail_account,
)

SETTINGS = FactSettings("aeca@gmail.com", frozenset(), frozenset())
BOSS_ONLY = FactSettings("aeca@gmail.com", frozenset({"boss@company.com"}), frozenset())


def test_encoded_names_subjects_and_charsets_decode():
    facts = extract_facts(
        make_raw(
            sender="=?utf-8?q?J=C3=BCrgen_M=C3=BCller?= <j@x.de>",
            subject="=?utf-8?q?=C3=84nderung?=",
            body_bytes=b"Gr\xfc\xdfe",
            charset="windows-1252",
        ),
        SETTINGS,
    )
    assert facts.sender_name == "Jürgen Müller"
    assert facts.subject == "Änderung"
    assert "Grüße" in facts.text


def test_an_undecodable_byte_does_not_crash():
    facts = extract_facts(make_raw(body_bytes=b"ok \xff\xfe", charset="utf-8"), SETTINGS)
    assert facts.text.startswith("ok")


def test_an_unknown_charset_does_not_crash():
    facts = extract_facts(make_raw(body_bytes=b"hello", charset="x-no-such-charset"), SETTINGS)
    assert facts.text.startswith("hello")


def test_a_raw_non_ascii_byte_in_a_header_does_not_crash_or_leave_a_surrogate():
    raw = make_raw()
    dirty = RawEmail(
        message_id=raw.message_id,
        thread_id=raw.thread_id,
        label_ids=raw.label_ids,
        rfc822=raw.rfc822.replace(b"Subject: Hello", b"Subject: Gr\xfc\xdfe"),
        internal_date=raw.internal_date,
    )
    facts = extract_facts(dirty, SETTINGS)
    facts.subject.encode("utf-8")


def test_sender_is_the_lowercased_address_without_the_display_name():
    facts = extract_facts(make_raw(sender='"The Boss" <Boss@Company.COM>'), SETTINGS)
    assert facts.sender == "boss@company.com" and facts.sender_name == "The Boss"


def test_a_missing_from_gives_an_empty_sender_that_is_never_internal():
    raw = make_raw()
    headerless = RawEmail(
        message_id=raw.message_id,
        thread_id=raw.thread_id,
        label_ids=raw.label_ids,
        rfc822=raw.rfc822.replace(b"From: sender@example.com\n", b""),
        internal_date=raw.internal_date,
    )
    facts = extract_facts(headerless, SETTINGS)
    assert facts.sender == "" and not facts.internal


def test_identifiers_labels_and_receipt_time_come_from_the_raw_email():
    moment = datetime(2026, 11, 6, 8, 30, tzinfo=UTC)
    facts = extract_facts(
        make_raw(
            message_id="g-9",
            thread_id="t-9",
            label_ids=["INBOX", "UNREAD"],
            internal_date=moment,
            headers={"Message-ID": "<abc@x.com>", "References": "<root@x.com> <abc@x.com>"},
        ),
        SETTINGS,
    )
    assert (facts.message_id, facts.thread_id) == ("g-9", "t-9")
    assert facts.label_ids == frozenset({"INBOX", "UNREAD"})
    assert facts.received_at == moment
    assert facts.rfc822_message_id == "<abc@x.com>"
    assert facts.references == "<root@x.com> <abc@x.com>"


def test_message_id_and_references_are_none_when_absent():
    facts = extract_facts(make_raw(), SETTINGS)
    assert facts.rfc822_message_id is None and facts.references is None


def test_a_naive_receipt_time_is_refused():
    with pytest.raises(ValueError, match="timezone"):
        RawEmail("m", "t", frozenset(), b"", datetime(2026, 11, 7, 9, 0))


def test_internal_matches_regardless_of_case_and_display_name():
    s = FactSettings("aeca@gmail.com", frozenset({"boss@company.com"}), frozenset())
    assert extract_facts(make_raw(sender='"The Boss" <Boss@Company.COM>', dmarc="pass"), s).internal


def test_an_internal_entry_in_another_case_still_matches():
    s = FactSettings("aeca@gmail.com", frozenset({"Boss@Company.COM"}), frozenset())
    assert extract_facts(make_raw(sender="boss@company.com", dmarc="pass"), s).internal


def test_an_internal_sender_whose_dmarc_failed_is_not_internal():
    s = FactSettings("aeca@gmail.com", frozenset({"boss@company.com"}), frozenset())
    assert not extract_facts(make_raw(sender="boss@company.com", dmarc="fail"), s).internal


def test_an_internal_sender_without_authentication_results_is_internal():
    assert extract_facts(make_raw(sender="boss@company.com"), BOSS_ONLY).internal


def test_a_forged_dmarc_pass_below_googles_fail_does_not_make_a_sender_internal():
    raw = make_raw(
        sender="boss@company.com",
        authentication_results=[
            "mx.google.com; dmarc=fail header.from=company.com",
            "mx.google.com; dmarc=pass header.from=company.com",
        ],
    )
    assert not extract_facts(raw, BOSS_ONLY).internal


def test_someone_not_listed_is_not_internal():
    assert not extract_facts(make_raw(sender="stranger@x.com", dmarc="pass"), BOSS_ONLY).internal


def test_with_no_internal_senders_listed_a_passing_sender_is_internal():
    assert extract_facts(make_raw(dmarc="pass"), SETTINGS).internal


def test_with_no_internal_senders_listed_a_failing_sender_is_not_internal():
    assert not extract_facts(make_raw(dmarc="fail"), SETTINGS).internal


def test_with_no_internal_senders_listed_a_sender_without_results_is_internal():
    assert extract_facts(make_raw(), SETTINGS).internal


@pytest.mark.parametrize(
    "headers,sender",
    [
        ({"Auto-Submitted": "auto-replied"}, "a@x.com"),
        ({"Precedence": "bulk"}, "a@x.com"),
        ({"Precedence": "list"}, "a@x.com"),
        ({"Precedence": "junk"}, "a@x.com"),
        ({"List-Id": "<news.x.com>"}, "a@x.com"),
        ({"List-Unsubscribe": "<mailto:u@x.com>"}, "a@x.com"),
        ({}, "noreply@x.com"),
        ({}, "No-Reply@x.com"),
        ({}, "mailer-daemon@x.com"),
        ({}, "postmaster@x.com"),
        ({}, "news@bulk.example"),
        ({}, "news@other.example"),
    ],
)
def test_automated_senders(headers, sender):
    s = FactSettings(
        "aeca@gmail.com", frozenset(), frozenset({"bulk.example", "news@other.example"})
    )
    assert extract_facts(make_raw(sender=sender, headers=headers), s).automated


def test_auto_submitted_no_is_not_automated():
    assert not extract_facts(make_raw(headers={"Auto-Submitted": "no"}), SETTINGS).automated


def test_an_ordinary_sender_is_neither_automated_nor_bulk():
    facts = extract_facts(make_raw(headers={"Precedence": "normal"}), SETTINGS)
    assert not facts.automated and not facts.bulk


def test_bulk_comes_from_the_list_or_the_headers_and_matches_whole_entries():
    s = FactSettings("aeca@gmail.com", frozenset(), frozenset({"bulk.example"}))
    assert extract_facts(make_raw(sender="a@bulk.example"), s).bulk
    assert extract_facts(make_raw(headers={"List-Id": "<x>"}), SETTINGS).bulk
    assert not extract_facts(make_raw(sender="a@notbulk.example"), s).bulk


def test_a_noreply_sender_is_automated_but_not_bulk():
    facts = extract_facts(make_raw(sender="noreply@x.com"), SETTINGS)
    assert facts.automated and not facts.bulk


def test_reply_to_differing_from_from_is_flagged():
    facts = extract_facts(
        make_raw(sender="a@x.com", headers={"Reply-To": "Other <Elsewhere@Y.com>"}), SETTINGS
    )
    assert facts.reply_to == "elsewhere@y.com" and facts.reply_to_differs


def test_reply_to_equal_to_from_in_another_case_is_not_flagged():
    facts = extract_facts(make_raw(sender="a@x.com", headers={"Reply-To": "A@X.com"}), SETTINGS)
    assert facts.reply_to == "a@x.com" and not facts.reply_to_differs


def test_no_reply_to_is_not_flagged():
    facts = extract_facts(make_raw(), SETTINGS)
    assert facts.reply_to is None and not facts.reply_to_differs


@pytest.mark.parametrize(
    "headers,expected",
    [
        ({"Importance": "high"}, True),
        ({"Importance": "High"}, True),
        ({"X-Priority": "1"}, True),
        ({"X-Priority": "1 (Highest)"}, True),
        ({"X-Priority": "3"}, False),
        ({"Importance": "normal"}, False),
        ({}, False),
    ],
)
def test_priority_headers(headers, expected):
    assert extract_facts(make_raw(headers=headers), SETTINGS).priority_high is expected


def test_aeca_headers_and_sent_label_are_read():
    facts = extract_facts(
        make_raw(
            label_ids=["SENT"],
            headers={"X-AECA-Kind": "approval-request", "X-AECA-Id": "appr-1"},
        ),
        SETTINGS,
    )
    assert (facts.aeca_kind, facts.aeca_id) == ("approval-request", "appr-1")
    assert facts.sent_by_account


def test_an_inbox_email_without_aeca_headers_is_not_ours():
    facts = extract_facts(make_raw(), SETTINGS)
    assert (facts.aeca_kind, facts.aeca_id) == (None, None)
    assert not facts.sent_by_account


def test_ical_uid_is_read_from_a_text_calendar_part():
    ics = (
        "BEGIN:VCALENDAR\r\nMETHOD:REQUEST\r\nBEGIN:VEVENT\r\n"
        "UID:abc123@google.com\r\nSUMMARY:Sync\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n"
    )
    assert extract_facts(make_raw(calendar=ics), SETTINGS).ical_uid == "abc123@google.com"


def test_a_folded_ical_uid_is_unfolded():
    ics = "BEGIN:VEVENT\r\nUID:abc\r\n def@google.com\r\nEND:VEVENT\r\n"
    assert extract_facts(make_raw(calendar=ics), SETTINGS).ical_uid == "abcdef@google.com"


def test_no_calendar_part_means_no_ical_uid():
    assert extract_facts(make_raw(), SETTINGS).ical_uid is None


def test_attachments_are_listed_by_name_and_type():
    facts = extract_facts(
        make_raw(attachments=[("report.pdf", "application/pdf"), ("a.png", "image/png")]), SETTINGS
    )
    assert facts.attachments == (
        Attachment("report.pdf", "application/pdf"),
        Attachment("a.png", "image/png"),
    )


def test_an_attachment_is_never_taken_for_the_body():
    facts = extract_facts(
        make_raw(body="the body", attachments=[("notes.txt", "text/plain")]), SETTINGS
    )
    assert facts.text == "the body"


def test_html_only_email_is_reduced_to_text():
    facts = extract_facts(
        make_raw(
            body=None,
            html="<style>p{}</style><script>evil()</script><p>Hello</p><p>there</p>",
        ),
        SETTINGS,
    )
    assert facts.text == "Hello\nthere"


def test_the_plain_part_wins_over_the_html_part():
    facts = extract_facts(make_raw(body="plain words", html="<p>html words</p>"), SETTINGS)
    assert facts.text == "plain words"


def test_text_is_bounded_to_the_model_limit_after_escaping():
    facts = extract_facts(make_raw(body="\x1b" * 9000), SETTINGS)
    assert len(facts.text) == 8000


def test_control_characters_in_subject_and_name_are_escaped():
    facts = extract_facts(make_raw(sender='"A\x1bB" <a@x.com>', subject="s\x07"), SETTINGS)
    assert facts.sender_name == "A\\x1bB" and facts.subject == "s\\x07"


def test_a_link_in_html_with_a_mismatched_target_is_reported():
    facts = extract_facts(
        make_raw(body=None, html='<a href="http://evil.example/l">https://paypal.com</a>'), SETTINGS
    )
    assert any(link.mismatched for link in facts.links)


def test_a_tracking_newsletter_has_no_mismatched_link():
    html = (
        '<a href="https://t.shop.example/c?id=1"><img src="x.png"></a>'
        '<a href="https://t.shop.example/c?id=2">Shop now</a>'
    )
    facts = extract_facts(make_raw(body=None, html=html), SETTINGS)
    assert len(facts.links) == 2 and not any(link.mismatched for link in facts.links)


def test_authentication_results_are_parsed_into_the_facts():
    facts = extract_facts(make_raw(dmarc="pass"), SETTINGS)
    assert (facts.auth.spf, facts.auth.dkim, facts.auth.dmarc) == ("pass", "pass", "pass")


@pytest.mark.parametrize(
    "a,b,expected",
    [
        ("A.B@Gmail.com", "ab@gmail.com", True),
        ("a.b@googlemail.com", "ab@gmail.com", True),
        ("a.b@company.com", "ab@company.com", False),
        ("A@Company.com", "a@company.com", True),
        ("a@gmail.com", "b@gmail.com", False),
    ],
)
def test_same_gmail_account_ignores_case_and_dots(a, b, expected):
    assert same_gmail_account(a, b) is expected


def test_facts_are_frozen():
    facts = extract_facts(make_raw(), SETTINGS)
    with pytest.raises(AttributeError):
        facts.internal = True  # type: ignore[misc]


def _raw_with_headers(header_block: bytes) -> RawEmail:
    base = make_raw()
    return RawEmail(
        message_id=base.message_id,
        thread_id=base.thread_id,
        label_ids=base.label_ids,
        rfc822=header_block + b"\r\n\r\nbody\r\n",
        internal_date=base.internal_date,
    )


@pytest.mark.parametrize(
    "from_header",
    [b"From: a <", b"From: a@x.com, <", b"From: =?bogus?q?x?= <"],
)
def test_an_unparseable_from_gives_an_empty_sender_instead_of_crashing(from_header):
    facts = extract_facts(_raw_with_headers(from_header), SETTINGS)
    assert facts.sender == "" and facts.internal is False


def test_an_unparseable_reply_to_gives_none_instead_of_crashing():
    raw = _raw_with_headers(b"From: a@x.com\r\nReply-To: =?bogus?q?x?= <")
    facts = extract_facts(raw, SETTINGS)
    assert facts.reply_to is None and facts.reply_to_differs is False


def test_unparseable_other_headers_do_not_crash():
    raw = _raw_with_headers(
        b"From: a@x.com\r\nMessage-ID: <\r\nSubject: =?bogus?q?x?=\r\nReferences: <\r\n"
        b"X-AECA-Kind: <\r\nX-AECA-Id: <"
    )
    facts = extract_facts(raw, SETTINGS)
    assert facts.sender == "a@x.com"


def test_an_unparseable_link_target_does_not_crash():
    facts = extract_facts(make_raw(html='<a href="http://[::1">x</a>'), SETTINGS)
    assert [link.mismatched for link in facts.links] == [False]


@pytest.mark.parametrize("name", ["\u202eevil", "a\u200bb"])
def test_invisible_characters_in_sender_name_and_subject_are_escaped(name):
    facts = extract_facts(make_raw(sender=f'"{name}" <a@x.com>', subject=name), SETTINGS)
    assert facts.sender_name == facts.subject
    assert not any(ord(c) in {0x202E, 0x200B} for c in facts.subject + facts.sender_name)


def test_a_line_separator_in_body_text_is_escaped():
    facts = extract_facts(make_raw(body="a\u2028b"), SETTINGS)
    assert facts.text == "a\\u2028b"


def test_an_empty_plain_part_falls_back_to_the_html_text():
    facts = extract_facts(make_raw(body="  \n", html="<p>Big newsletter news</p>"), SETTINGS)
    assert "Big newsletter news" in facts.text


def test_links_are_capped_at_the_first_200():
    anchors = "".join(f'<a href="https://x.example/{n}">l{n}</a>' for n in range(1000))
    facts = extract_facts(make_raw(html=anchors), SETTINGS)
    assert len(facts.links) == 200 and facts.links[0].target.endswith("/0")


def test_link_text_and_target_are_clipped_to_500_characters():
    html = f'<a href="https://x.example/{"p" * 2000}">{"t" * 2000}</a>'
    link = extract_facts(make_raw(html=html), SETTINGS).links[0]
    assert len(link.text) == 500 and len(link.target) == 500


def test_subject_and_sender_name_are_bounded_to_500_characters():
    facts = extract_facts(
        make_raw(sender=f'"{"n" * 10000}" <a@x.com>', subject="s" * 10000), SETTINGS
    )
    assert len(facts.subject) == 500 and len(facts.sender_name) == 500


def test_references_are_bounded_to_2000_characters():
    raw = make_raw(headers={"References": " ".join(f"<{n}@x.com>" for n in range(2000))})
    facts = extract_facts(raw, SETTINGS)
    assert facts.references is not None and len(facts.references) == 2000


def test_reply_to_is_bounded_to_500_characters_but_still_differs():
    facts = extract_facts(make_raw(headers={"Reply-To": f"{'r' * 10000}@x.com"}), SETTINGS)
    assert facts.reply_to is not None and len(facts.reply_to) == 500
    assert facts.reply_to_differs is True


def test_one_unreadable_authentication_header_does_not_hide_googles(monkeypatch):
    from aeca.domain import facts as facts_module

    class Unreadable:
        def __str__(self) -> str:
            raise ValueError("unreadable")

    class Message:
        def get_all(self, name, default):
            return [Unreadable(), "mx.google.com; dmarc=fail"]

    assert facts_module._all_headers(Message(), "Authentication-Results") == [
        "mx.google.com; dmarc=fail"
    ]
