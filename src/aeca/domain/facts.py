"""Turns a raw email into the typed facts that every later step reads.

Pure code: nothing here asks the model or the network anything.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from email.errors import UndecodableBytesDefect
from email.headerregistry import AddressHeader
from email.message import EmailMessage

from aeca.domain.auth_results import AuthResults, parse_authentication_results
from aeca.domain.email import (
    RawEmail,
    attachment_parts,
    body_text,
    calendar_uid,
    parse_message,
)
from aeca.domain.labels import SENT
from aeca.domain.links import Link, extract_links
from aeca.domain.text import bounded, html_to_text, visible

MODEL_TEXT_LIMIT = 8000
NAME_LIMIT = 255
BULK_PRECEDENCES = frozenset({"bulk", "list", "junk"})
BULK_HEADERS = ("List-Id", "List-Unsubscribe")
SUBJECT_LIMIT = 500
SENDER_NAME_LIMIT = 500
REFERENCES_LIMIT = 2000
REPLY_TO_LIMIT = 500
HIGH_X_PRIORITIES = frozenset({"1", "2"})
AUTOMATED_LOCAL_PARTS = frozenset({"noreply", "no-reply", "mailer-daemon", "postmaster"})
GMAIL_DOMAINS = frozenset({"gmail.com", "googlemail.com"})
CANONICAL_GMAIL_DOMAIN = "gmail.com"
MISSING_DEFECTS = "the header records no defects attribute"
HEADER_BLOCK_END = b"\r\n\r\n"
BARE_LINE_END = re.compile(rb"\r(?!\n)|(?<!\r)\n")
# A From field as RFC 5322's obsolete syntax allows it: whitespace may stand before the colon.
FROM_LINE = re.compile(rb"^from[ \t]*:", re.IGNORECASE | re.MULTILINE)


@dataclass(frozen=True, slots=True)
class Attachment:
    name: str
    mime_type: str


@dataclass(frozen=True, slots=True)
class FactSettings:
    account_address: str
    internal_senders: frozenset[str]
    bulk_senders: frozenset[str]


@dataclass(frozen=True, slots=True)
class EmailFacts:
    message_id: str
    thread_id: str
    label_ids: frozenset[str]
    received_at: datetime
    rfc822_message_id: str | None
    references: str | None
    sender: str
    sender_name: str
    # One From header holding one address. Otherwise the sender AECA reads may not be the one
    # Gmail's DMARC result was judged for.
    single_sender: bool
    reply_to: str | None
    reply_to_differs: bool
    subject: str
    auth: AuthResults
    internal: bool
    automated: bool
    bulk: bool
    priority_high: bool
    links: tuple[Link, ...]
    attachments: tuple[Attachment, ...]
    text: str
    aeca_kind: str | None
    aeca_id: str | None
    ical_uid: str | None

    @property
    def sent_by_account(self) -> bool:
        return SENT in self.label_ids


def extract_facts(raw: RawEmail, settings: FactSettings) -> EmailFacts:
    message = parse_message(raw)
    sender_name, sender = _first_address(message, "From")
    _, reply_to_address = _first_address(message, "Reply-To")
    reply_to = reply_to_address or None
    auth = parse_authentication_results(_all_headers(message, "Authentication-Results"))
    single_sender = _headers_are_clean(raw, message) and _has_a_single_sender(message)
    bulk = _is_bulk(message, sender, settings)
    plain, html = body_text(message)
    return EmailFacts(
        message_id=raw.message_id,
        thread_id=raw.thread_id,
        label_ids=raw.label_ids,
        received_at=raw.internal_date,
        rfc822_message_id=_header(message, "Message-ID"),
        references=_bounded_optional(_header(message, "References"), REFERENCES_LIMIT),
        sender=sender,
        sender_name=bounded(visible(sender_name), SENDER_NAME_LIMIT),
        single_sender=single_sender,
        reply_to=_bounded_optional(reply_to, REPLY_TO_LIMIT),
        reply_to_differs=reply_to is not None and reply_to != sender,
        subject=bounded(visible(_header(message, "Subject") or ""), SUBJECT_LIMIT),
        auth=auth,
        # An ambiguous From never counts as internal, as it never counts as the manager (R40).
        internal=single_sender and _counts_as_internal(sender, auth, settings),
        automated=bulk or _is_automated(message, sender),
        bulk=bulk,
        priority_high=_is_priority_high(message),
        links=extract_links(html, plain or ""),
        attachments=_attachments(message),
        text=_model_text(plain, html),
        aeca_kind=_header(message, "X-AECA-Kind"),
        aeca_id=_header(message, "X-AECA-Id"),
        ical_uid=calendar_uid(message),
    )


def same_gmail_account(a: str, b: str) -> bool:
    return _canonical_account(a) == _canonical_account(b)


def _canonical_account(address: str) -> str:
    local, _, domain = address.strip().lower().rpartition("@")
    if domain in GMAIL_DOMAINS:
        return f"{local.replace('.', '')}@{CANONICAL_GMAIL_DOMAIN}"
    return f"{local}@{domain}"


def _bounded_optional(text: str | None, limit: int) -> str | None:
    return None if text is None else bounded(visible(text), limit)


def _raw_header(message: EmailMessage, name: str) -> object | None:
    """The stdlib parses a header lazily, and a hostile one (`From: a <`) raises from deep inside
    that parser. Whatever it raises, the header counts as absent: no email may crash extraction."""
    try:
        header: object | None = message[name]
    except Exception:
        return None
    return header


def _header(message: EmailMessage, name: str) -> str | None:
    value = _raw_header(message, name)
    if value is None:
        return None
    try:
        return str(value).strip() or None
    except Exception:
        return None


def _all_headers(message: EmailMessage, name: str) -> list[str]:
    try:
        values = list(message.get_all(name, []))
    except Exception:
        return []
    return [text for text in (_readable(value) for value in values) if text is not None]


def _readable(value: object) -> str | None:
    try:
        return str(value)
    except Exception:
        return None


def _first_address(message: EmailMessage, header_name: str) -> tuple[str, str]:
    """The display name and the lowercased address, both empty when the header has none."""
    header = _raw_header(message, header_name)
    try:
        if not isinstance(header, AddressHeader) or not header.addresses:
            return "", ""
        first = header.addresses[0]
        return first.display_name, first.addr_spec.lower()
    except Exception:
        return "", ""


# Ruling R43: Python can drop a second From before the header parser sees it, as a `From :` line
# (a defect on the message alone, or none at all as the first or the last header line, which it
# takes for an envelope line or moves into the body) or a line split off by a bare CR (no defect
# anywhere). So the raw header block itself must hold exactly one From line, and no bare line end.
# Every real delivered message measured passes.
def _headers_are_clean(raw: RawEmail, message: EmailMessage) -> bool:
    if not _only_undecodable_bytes(message.defects):
        return False
    header_block = _header_block(raw.rfc822)
    if BARE_LINE_END.search(header_block) is not None:
        return False
    return len(FROM_LINE.findall(header_block)) == 1


def _header_block(rfc822: bytes) -> bytes:
    end = rfc822.find(HEADER_BLOCK_END)
    return rfc822 if end == -1 else rfc822[:end]


def _only_undecodable_bytes(defects: Sequence[object]) -> bool:
    return all(isinstance(defect, UndecodableBytesDefect) for defect in defects)


# A structural defect means the parser dropped part of the header to find an address (for
# example `boss@x <attacker@x>`), so the address read may not be the one Gmail judged DMARC for.
# Undecodable bytes alone are a raw UTF-8 display name, which some clients send unencoded: they are
# accepted (ruling R42), but only around an address that is itself plain ASCII.
def _has_a_single_sender(message: EmailMessage) -> bool:
    try:
        headers = list(message.get_all("From", []))
        if len(headers) != 1 or not isinstance(headers[0], AddressHeader):
            return False
        header = headers[0]
        # The header registry mixes BaseHeader, which owns `defects`, into every header class; the
        # stubs don't say so. A header without it counts as defective.
        defects: tuple[object, ...] = getattr(header, "defects", (MISSING_DEFECTS,))
        if not _only_undecodable_bytes(defects):
            return False
        if len(header.addresses) != 1:
            return False
        address = header.addresses[0].addr_spec
        return bool(address) and address.isascii()
    except Exception:
        return False


def _counts_as_internal(sender: str, auth: AuthResults, settings: FactSettings) -> bool:
    if not sender or auth.dmarc == "fail":
        return False
    listed = {entry.lower() for entry in settings.internal_senders}
    return not listed or sender in listed


def _is_bulk(message: EmailMessage, sender: str, settings: FactSettings) -> bool:
    return _on_bulk_list(sender, settings) or _has_bulk_headers(message)


def _on_bulk_list(sender: str, settings: FactSettings) -> bool:
    listed = {entry.lower() for entry in settings.bulk_senders}
    return sender in listed or sender.rpartition("@")[2] in listed


def _has_bulk_headers(message: EmailMessage) -> bool:
    precedence = (_header(message, "Precedence") or "").lower()
    return precedence in BULK_PRECEDENCES or any(
        _raw_header(message, name) is not None for name in BULK_HEADERS
    )


def _is_automated(message: EmailMessage, sender: str) -> bool:
    return _declares_auto_submission(message) or sender.partition("@")[0] in AUTOMATED_LOCAL_PARTS


def _declares_auto_submission(message: EmailMessage) -> bool:
    value = _raw_header(message, "Auto-Submitted")
    return value is not None and str(value).split(";")[0].strip().lower() != "no"


def _is_priority_high(message: EmailMessage) -> bool:
    importance = (_header(message, "Importance") or "").lower()
    priority_words = (_header(message, "X-Priority") or "").split()
    return importance == "high" or bool(priority_words and priority_words[0] in HIGH_X_PRIORITIES)


def _attachments(message: EmailMessage) -> tuple[Attachment, ...]:
    return tuple(
        Attachment(
            name=bounded(visible(part.get_filename() or ""), NAME_LIMIT),
            mime_type=part.get_content_type(),
        )
        for part in attachment_parts(message)
    )


def _model_text(plain: str | None, html: str | None) -> str:
    chosen = plain if plain and plain.strip() else html_to_text(html or "")
    return bounded(visible(chosen).strip(), MODEL_TEXT_LIMIT)
