"""Builds raw RFC 822 emails for tests, so no test needs a mailbox."""

from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, datetime
from email.message import EmailMessage

from aeca.domain.email import RawEmail

GOOGLE_SERVER = "mx.google.com"
DEFAULT_RECEIVED_AT = datetime(2026, 11, 7, 9, 0, tzinfo=UTC)


def make_raw(
    *,
    sender: str = "sender@example.com",
    to: str = "aeca@gmail.com",
    subject: str = "Hello",
    body: str | None = "Hi there",
    body_bytes: bytes | None = None,
    charset: str = "utf-8",
    html: str | None = None,
    attachments: Iterable[tuple[str, str]] = (),
    calendar: str | None = None,
    headers: Mapping[str, str] | None = None,
    dmarc: str | None = None,
    authentication_results: Sequence[str] = (),
    label_ids: Iterable[str] = ("INBOX",),
    message_id: str = "gmail-1",
    thread_id: str = "thread-1",
    internal_date: datetime = DEFAULT_RECEIVED_AT,
) -> RawEmail:
    message = EmailMessage()
    for value in _authentication_results(dmarc, authentication_results):
        message["Authentication-Results"] = value
    message["From"] = sender
    message["To"] = to
    message["Subject"] = subject
    for name, value in (headers or {}).items():
        message[name] = value
    if body_bytes is not None:
        rfc822 = _with_raw_body(message, body_bytes, charset)
    else:
        _fill_body(message, body=body, html=html, calendar=calendar)
        for name, mime_type in attachments:
            maintype, _, subtype = mime_type.partition("/")
            message.add_attachment(b"%PDF", maintype=maintype, subtype=subtype, filename=name)
        rfc822 = _as_gmail_bytes(message)
    return RawEmail(
        message_id=message_id,
        thread_id=thread_id,
        label_ids=frozenset(label_ids),
        rfc822=rfc822,
        internal_date=internal_date,
    )


def _authentication_results(dmarc: str | None, explicit: Sequence[str]) -> list[str]:
    if explicit:
        return list(explicit)
    if dmarc is None:
        return []
    return [f"{GOOGLE_SERVER}; spf=pass; dkim=pass; dmarc={dmarc} header.from=example.com"]


def _fill_body(
    message: EmailMessage, *, body: str | None, html: str | None, calendar: str | None
) -> None:
    if body is not None:
        message.set_content(body)
        if html is not None:
            message.add_alternative(html, subtype="html")
    elif html is not None:
        message.set_content(html, subtype="html")
    else:
        message.set_content("")
    if calendar is not None:
        message.add_alternative(calendar, subtype="calendar")


def _with_raw_body(message: EmailMessage, body_bytes: bytes, charset: str) -> bytes:
    """Bytes are spliced in after the headers, since EmailMessage only takes text it can encode."""
    message["Content-Type"] = f'text/plain; charset="{charset}"'
    message["Content-Transfer-Encoding"] = "8bit"
    return _as_gmail_bytes(message) + b"\r\n" + body_bytes


# Gmail hands over every line ending in CRLF (measured), and AECA refuses a bare line end in the
# headers, so test emails are written the same way.
def _as_gmail_bytes(message: EmailMessage) -> bytes:
    return message.as_bytes(policy=message.policy.clone(linesep="\r\n"))
