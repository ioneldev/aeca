"""The raw email as Gmail hands it over, and the MIME reading every fact starts from."""

import re
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from email import message_from_bytes, policy
from email.message import EmailMessage

DEFAULT_CHARSET = "utf-8"
ICAL_UID_LINE = re.compile(r"^UID[^:\r\n]*:(.+)$", re.MULTILINE | re.IGNORECASE)
ICAL_LINE_FOLD = re.compile(r"\r?\n[ \t]")


@dataclass(frozen=True, slots=True)
class RawEmail:
    message_id: str
    thread_id: str
    label_ids: frozenset[str]
    rfc822: bytes
    internal_date: datetime

    def __post_init__(self) -> None:
        if self.internal_date.tzinfo is None:
            raise ValueError("internal_date must carry a timezone")


def parse_message(raw: RawEmail) -> EmailMessage:
    return message_from_bytes(raw.rfc822, policy=policy.default)


def body_text(message: EmailMessage) -> tuple[str | None, str | None]:
    """The first plain part and the first HTML part, each decoded, or None when absent."""
    plain = _first_decoded_body(message, "text/plain")
    html = _first_decoded_body(message, "text/html")
    return plain, html


def attachment_parts(message: EmailMessage) -> Iterator[EmailMessage]:
    return (part for part in _leaf_parts(message) if _is_attachment(part))


def calendar_uid(message: EmailMessage) -> str | None:
    for part in _leaf_parts(message):
        if part.get_content_type() == "text/calendar":
            unfolded = ICAL_LINE_FOLD.sub("", decode_part(part))
            found = ICAL_UID_LINE.search(unfolded)
            if found:
                return found.group(1).strip()
    return None


def decode_part(part: EmailMessage) -> str:
    payload = part.get_payload(decode=True)
    if not isinstance(payload, bytes):
        return ""
    return payload.decode(_known_charset(part), errors="replace")


def _first_decoded_body(message: EmailMessage, content_type: str) -> str | None:
    for part in _leaf_parts(message):
        if part.get_content_type() == content_type and not _is_attachment(part):
            return decode_part(part)
    return None


def _leaf_parts(message: EmailMessage) -> Iterator[EmailMessage]:
    for part in message.walk():
        if not part.is_multipart():
            yield part


def _is_attachment(part: EmailMessage) -> bool:
    return part.get_content_disposition() == "attachment" or bool(part.get_filename())


def _known_charset(part: EmailMessage) -> str:
    declared = part.get_content_charset()
    try:
        "".encode(declared or DEFAULT_CHARSET)
    except LookupError:
        return DEFAULT_CHARSET
    return declared or DEFAULT_CHARSET
