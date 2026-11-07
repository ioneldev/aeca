"""The test corpus, and the two ways it becomes an email: as Gmail would deliver it, for the eval,
and as a message to send, for the rehearsal. Test tooling: no domain or application code imports it.
"""

import json
from datetime import datetime
from email.headerregistry import Address
from email.message import EmailMessage
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, ValidationError

from aeca.domain.email import RawEmail
from aeca.domain.errors import ConfigError
from aeca.domain.labels import Label

SenderRole = Literal["internal", "external", "bulk", "automated"]
CalendarExpectation = Literal["free", "clash", "all_clash"]

EVAL_ACCOUNT_ADDRESS = "aeca@aeca-eval.test"
EVAL_INTERNAL_SENDER = "internal.tester@aeca-eval.test"
EVAL_EXTERNAL_SENDER = "external.tester@aeca-eval.test"

REHEARSAL_HEADER = "X-AECA-Test"
REHEARSAL_MARKER = "rehearsal"
AUTHENTICATION_RESULTS = "Authentication-Results"
GMAIL_SERVER = "mx.google.com"
# Headers that would change who receives the message, or what it is, so a corpus entry may not set
# them: the recipient is always the account under test.
RESERVED_HEADERS = frozenset(
    {"to", "cc", "bcc", "from", "message-id", "subject", "in-reply-to", "references"}
)
REPLY_PREFIX = "Re: "


class Attachment(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    mime_type: str


class Expected(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    label: Label
    urgent: bool
    actions: list[str]
    approval: bool
    reply: str
    calendar: CalendarExpectation | None


class CorpusEmail(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    sender_role: SenderRole
    from_name: str
    subject: str
    body: str
    html: bool
    headers: dict[str, str]
    deliverable: bool
    expected: Expected
    note: str = ""
    follows: str | None = None
    attachments: list[Attachment] = []

    @property
    def message_id(self) -> str:
        return corpus_message_id(self.id)


def corpus_message_id(email_id: str) -> str:
    return f"<corpus-{email_id}@aeca.test>"


def load_corpus(path: Path) -> tuple[CorpusEmail, ...]:
    try:
        entries = json.loads(path.read_text(encoding="utf-8"))
        corpus = tuple(_parsed(index, entry) for index, entry in enumerate(entries))
    except (OSError, ValueError, TypeError) as error:
        raise _unusable(path, _describe(error)) from error
    problem = _structural_problem(corpus)
    if problem:
        raise _unusable(path, problem)
    return corpus


def _parsed(index: int, entry: object) -> CorpusEmail:
    try:
        return CorpusEmail.model_validate(entry)
    except ValidationError as error:
        raise ValueError(f"email #{index}, {_describe(error)}") from error


def to_raw(email: CorpusEmail, sender: str, *, received_at: datetime) -> RawEmail:
    """The bytes Gmail would hand over: it adds the Authentication-Results header, and an email
    that is not deliverable gets only the headers the corpus itself gives it."""
    message = _compose(email, sender, recipient=EVAL_ACCOUNT_ADDRESS)
    _add_authentication_result(message, email, sender)
    return RawEmail(
        message_id=f"corpus-{email.id}",
        thread_id=f"corpus-thread-{email.follows or email.id}",
        label_ids=frozenset({"INBOX"}),
        # Gmail hands every line over ending in CRLF, and facts refuse a bare line end in headers.
        rfc822=message.as_bytes(policy=message.policy.clone(linesep="\r\n")),
        internal_date=received_at,
    )


def to_mime(
    email: CorpusEmail, sender: str, *, recipient: str, parent: CorpusEmail | None = None
) -> EmailMessage:
    message = _compose(email, sender, recipient=recipient)
    message[REHEARSAL_HEADER] = REHEARSAL_MARKER
    if parent is not None:
        del message["Subject"]
        message["Subject"] = _reply_subject(email.subject, parent.subject)
    return message


def _compose(email: CorpusEmail, sender: str, *, recipient: str) -> EmailMessage:
    message = EmailMessage()
    message["From"] = Address(display_name=email.from_name, addr_spec=sender)
    message["To"] = recipient
    message["Subject"] = email.subject
    message["Message-ID"] = email.message_id
    for name, value in email.headers.items():
        message[name] = value
    if email.follows is not None:
        message["In-Reply-To"] = message["References"] = corpus_message_id(email.follows)
    _set_body(message, email)
    for attachment in email.attachments:
        _attach_placeholder(message, attachment)
    return message


def _set_body(message: EmailMessage, email: CorpusEmail) -> None:
    if email.html:
        message.set_content(email.body, subtype="html")
    else:
        message.set_content(email.body)


def _attach_placeholder(message: EmailMessage, attachment: Attachment) -> None:
    maintype, _, subtype = attachment.mime_type.partition("/")
    message.add_attachment(
        f"placeholder for {attachment.name}".encode(),
        maintype=maintype,
        subtype=subtype,
        filename=attachment.name,
    )


def _add_authentication_result(message: EmailMessage, email: CorpusEmail, sender: str) -> None:
    if AUTHENTICATION_RESULTS in message:
        return
    domain = sender.rpartition("@")[2]
    if email.deliverable:
        message[AUTHENTICATION_RESULTS] = f"{GMAIL_SERVER}; dmarc=pass header.from={domain}"
    elif email.sender_role == "internal":
        message[AUTHENTICATION_RESULTS] = f"{GMAIL_SERVER}; dmarc=fail header.from={domain}"


def _reply_subject(subject: str, parent_subject: str) -> str:
    """Gmail threads by subject as well as by reference headers, so a reply must carry the
    parent's subject whatever the corpus gave it."""
    reply_subject = f"{REPLY_PREFIX}{parent_subject}"
    return subject if subject.lower().startswith(reply_subject.lower()) else reply_subject


def _structural_problem(corpus: tuple[CorpusEmail, ...]) -> str | None:
    seen: set[str] = set()
    for email in corpus:
        if email.id in seen:
            return f"the id {email.id!r} appears twice"
        reserved = sorted(name for name in email.headers if name.lower() in RESERVED_HEADERS)
        if reserved:
            return f"{email.id!r} sets the reserved header {reserved[0]!r}"
        if email.follows is not None and email.follows not in seen:
            return f"{email.id!r} follows {email.follows!r}, which is not an earlier email"
        seen.add(email.id)
    return None


def _describe(error: Exception) -> str:
    if isinstance(error, ValidationError):
        return "; ".join(
            f"{'.'.join(str(part) for part in problem['loc'])}: {problem['msg']}"
            for problem in error.errors(include_input=False)
        )
    if isinstance(error, OSError):
        return error.strerror or "it cannot be read"
    if isinstance(error, json.JSONDecodeError | TypeError):
        return "it is not a JSON list of emails"
    return str(error)


def _unusable(path: Path, problem: str) -> ConfigError:
    return ConfigError(
        f"The corpus {path} cannot be used: {problem}.",
        next_action=f"Fix {path} (or pass --corpus <file>), then run again.",
    )
