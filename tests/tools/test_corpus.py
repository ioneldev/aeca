import json
from pathlib import Path

import pytest
from fakes.fixed_clock import FIXED_NOW
from support.corpus import make_corpus_email

from aeca.domain.errors import ConfigError
from aeca.domain.facts import extract_facts
from aeca.domain.labels import Label
from aeca.tools.corpus import (
    EVAL_EXTERNAL_SENDER,
    EVAL_INTERNAL_SENDER,
    CorpusEmail,
    load_corpus,
    to_mime,
    to_raw,
)
from aeca.tools.evaluate import EVAL_FACT_SETTINGS

CORPUS_PATH = Path(__file__).resolve().parents[1] / "corpus" / "emails.json"


def facts_of(email: CorpusEmail, sender: str = EVAL_EXTERNAL_SENDER):  # type: ignore[no-untyped-def]
    return extract_facts(to_raw(email, sender, received_at=FIXED_NOW), EVAL_FACT_SETTINGS)


def write_corpus(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def test_the_shipped_corpus_loads_and_has_unique_ids() -> None:
    corpus = load_corpus(CORPUS_PATH)

    assert len(corpus) >= 25
    assert len({email.id for email in corpus}) == len(corpus)


def test_every_expected_label_is_a_label() -> None:
    labels = {label.value for label in Label}

    assert {email.expected.label for email in load_corpus(CORPUS_PATH)} <= labels


def test_a_corpus_with_an_unknown_expected_label_is_refused(tmp_path: Path) -> None:
    bad = make_corpus_email().model_dump(mode="json")
    bad["expected"]["label"] = "PROMOTIONS"
    path = write_corpus(tmp_path / "c.json", json.dumps([bad]))

    with pytest.raises(ConfigError, match=r"expected\.label"):
        load_corpus(path)


def test_follows_must_name_an_earlier_email(tmp_path: Path) -> None:
    later = make_corpus_email(id="later").model_dump(mode="json")
    first = make_corpus_email(id="first", follows="later").model_dump(mode="json")
    path = write_corpus(tmp_path / "c.json", json.dumps([first, later]))

    with pytest.raises(ConfigError, match=r"first.*later"):
        load_corpus(path)


def test_duplicate_ids_are_refused(tmp_path: Path) -> None:
    one = make_corpus_email(id="same").model_dump(mode="json")
    path = write_corpus(tmp_path / "c.json", json.dumps([one, one]))

    with pytest.raises(ConfigError, match="same"):
        load_corpus(path)


def test_an_unreadable_corpus_says_what_to_do(tmp_path: Path) -> None:
    with pytest.raises(ConfigError) as raised:
        load_corpus(tmp_path / "missing.json")

    assert raised.value.next_action


def test_a_deliverable_internal_email_arrives_authenticated_from_the_internal_sender() -> None:
    facts = facts_of(make_corpus_email(sender_role="internal"), EVAL_INTERNAL_SENDER)

    assert facts.auth.dmarc == "pass" and facts.internal is True
    assert facts.sender == EVAL_INTERNAL_SENDER


def test_an_external_email_is_not_internal() -> None:
    facts = facts_of(make_corpus_email(sender_role="external"))

    assert facts.internal is False and facts.sender == EVAL_EXTERNAL_SENDER


def test_a_forged_internal_email_without_headers_fails_dmarc() -> None:
    forged = make_corpus_email(sender_role="internal", deliverable=False)

    facts = facts_of(forged, EVAL_INTERNAL_SENDER)

    assert facts.auth.dmarc == "fail" and facts.internal is False


def test_an_undeliverable_external_email_without_headers_has_no_authentication_result() -> None:
    facts = facts_of(make_corpus_email(deliverable=False))

    assert facts.auth.dmarc is None


def test_the_headers_the_corpus_gives_win_over_the_defaults() -> None:
    phishing = make_corpus_email(
        deliverable=False,
        headers={"Authentication-Results": "mx.google.com; dmarc=fail header.from=evil.test"},
    )

    assert facts_of(phishing).auth.dmarc == "fail"


def test_bulk_and_automated_signs_come_from_the_corpus_headers() -> None:
    bulk = make_corpus_email(sender_role="bulk", headers={"List-Unsubscribe": "<mailto:u@x.test>"})
    automated = make_corpus_email(
        sender_role="automated", headers={"Auto-Submitted": "auto-generated"}
    )

    assert facts_of(bulk).bulk is True
    assert facts_of(automated).automated is True


def test_attachments_are_generated_placeholders_of_the_stated_type() -> None:
    invoice = make_corpus_email(attachments=[{"name": "INV-1.pdf", "mime_type": "application/pdf"}])

    attachments = facts_of(invoice).attachments

    assert [(a.name, a.mime_type) for a in attachments] == [("INV-1.pdf", "application/pdf")]


def test_html_only_emails_have_no_text_part_but_still_yield_text() -> None:
    page = make_corpus_email(html=True, body="<p>Please review the <b>contract</b></p>")

    assert "contract" in facts_of(page).text


def test_a_follow_up_points_at_the_message_it_follows() -> None:
    reply = make_corpus_email(id="second", follows="first")

    facts = facts_of(reply)

    assert facts.rfc822_message_id == "<corpus-second@aeca.test>"
    assert facts.references == "<corpus-first@aeca.test>"


def test_a_follow_up_shares_the_thread_of_the_email_it_follows() -> None:
    first = to_raw(make_corpus_email(id="first"), EVAL_EXTERNAL_SENDER, received_at=FIXED_NOW)
    second = to_raw(
        make_corpus_email(id="second", follows="first"), EVAL_EXTERNAL_SENDER, received_at=FIXED_NOW
    )

    assert first.thread_id == second.thread_id
    assert first.message_id != second.message_id


def test_mime_carries_the_test_header_the_message_id_and_the_recipient() -> None:
    message = to_mime(make_corpus_email(id="x"), "sender@gmail.test", recipient="aeca@gmail.test")

    assert message["X-AECA-Test"] == "rehearsal"
    assert message["Message-ID"] == "<corpus-x@aeca.test>"
    assert message["To"] == "aeca@gmail.test"
    assert "sender@gmail.test" in message["From"]


def test_mime_of_a_follow_up_is_a_reply_in_the_parent_thread() -> None:
    parent = make_corpus_email(id="first", subject="Price list")
    reply = make_corpus_email(id="second", follows="first", subject="Re: Something else")

    message = to_mime(reply, "s@gmail.test", recipient="a@gmail.test", parent=parent)

    assert message["Subject"] == "Re: Price list"
    assert message["In-Reply-To"] == "<corpus-first@aeca.test>"
    assert message["References"] == "<corpus-first@aeca.test>"


def test_mime_does_not_double_the_reply_prefix() -> None:
    parent = make_corpus_email(id="first", subject="Price list")
    reply = make_corpus_email(id="second", follows="first", subject="RE: price list - thanks")

    message = to_mime(reply, "s@gmail.test", recipient="a@gmail.test", parent=parent)

    assert message["Subject"] == "RE: price list - thanks"


@pytest.mark.parametrize(
    "name",
    ["To", "cc", "BCC", "From", "message-id", "SUBJECT", "In-Reply-To", "references"],
)
def test_a_corpus_header_may_not_redirect_or_rewrite_the_message(tmp_path: Path, name: str) -> None:
    sneaky = make_corpus_email(headers={name: "x@elsewhere.test"}).model_dump(mode="json")
    path = write_corpus(tmp_path / "c.json", json.dumps([sneaky]))

    with pytest.raises(ConfigError, match=rf"(?i){name}"):
        load_corpus(path)


def test_the_shipped_corpus_has_no_reserved_headers() -> None:
    assert load_corpus(CORPUS_PATH)


def test_the_raw_email_has_crlf_line_ends_as_gmail_hands_it_over() -> None:
    raw = to_raw(make_corpus_email(id="crlf"), EVAL_EXTERNAL_SENDER, received_at=FIXED_NOW)

    header_block = raw.rfc822.split(b"\r\n\r\n", 1)[0]
    assert b"\r\n" in header_block
    assert b"\n" not in header_block.replace(b"\r\n", b"")
