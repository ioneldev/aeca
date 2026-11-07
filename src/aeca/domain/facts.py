"""Turns a raw email into the typed facts that every later step reads.

Pure code: nothing here asks the model or the network anything.
"""

from dataclasses import dataclass
from datetime import datetime
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
from aeca.domain.links import Link, extract_links
from aeca.domain.text import bounded, html_to_text, visible

MODEL_TEXT_LIMIT = 8000
NAME_LIMIT = 255
BULK_PRECEDENCES = frozenset({"bulk", "list", "junk"})
BULK_HEADERS = ("List-Id", "List-Unsubscribe")
HIGH_X_PRIORITIES = frozenset({"1", "2"})
AUTOMATED_LOCAL_PARTS = frozenset({"noreply", "no-reply", "mailer-daemon", "postmaster"})
GMAIL_DOMAINS = frozenset({"gmail.com", "googlemail.com"})
CANONICAL_GMAIL_DOMAIN = "gmail.com"


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
        return "SENT" in self.label_ids


def extract_facts(raw: RawEmail, settings: FactSettings) -> EmailFacts:
    message = parse_message(raw)
    sender_name, sender = _first_address(message, "From")
    _, reply_to_address = _first_address(message, "Reply-To")
    reply_to = reply_to_address or None
    auth = parse_authentication_results(_all_headers(message, "Authentication-Results"))
    bulk = _is_bulk(message, sender, settings)
    plain, html = body_text(message)
    return EmailFacts(
        message_id=raw.message_id,
        thread_id=raw.thread_id,
        label_ids=raw.label_ids,
        received_at=raw.internal_date,
        rfc822_message_id=_header(message, "Message-ID"),
        references=_header(message, "References"),
        sender=sender,
        sender_name=visible(sender_name),
        reply_to=reply_to,
        reply_to_differs=reply_to is not None and reply_to != sender,
        subject=visible(_header(message, "Subject") or ""),
        auth=auth,
        internal=_counts_as_internal(sender, auth, settings),
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


def _header(message: EmailMessage, name: str) -> str | None:
    value = message[name]
    return str(value).strip() or None if value is not None else None


def _all_headers(message: EmailMessage, name: str) -> list[str]:
    return [str(value) for value in message.get_all(name, [])]


def _first_address(message: EmailMessage, header_name: str) -> tuple[str, str]:
    """The display name and the lowercased address, both empty when the header has none."""
    header = message[header_name]
    if not isinstance(header, AddressHeader) or not header.addresses:
        return "", ""
    first = header.addresses[0]
    return first.display_name, first.addr_spec.lower()


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
    return precedence in BULK_PRECEDENCES or any(message[name] is not None for name in BULK_HEADERS)


def _is_automated(message: EmailMessage, sender: str) -> bool:
    return _declares_auto_submission(message) or sender.partition("@")[0] in AUTOMATED_LOCAL_PARTS


def _declares_auto_submission(message: EmailMessage) -> bool:
    value = message["Auto-Submitted"]
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
    chosen = plain if plain is not None else html_to_text(html or "")
    return bounded(visible(chosen).strip(), MODEL_TEXT_LIMIT)
