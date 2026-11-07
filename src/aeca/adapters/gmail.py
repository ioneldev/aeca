"""Gmail behind the Mailbox port. Sends go through drafts so each one is sent exactly once."""

import base64
from collections.abc import Sequence
from datetime import UTC, datetime
from email import policy
from email.message import EmailMessage
from email.utils import parseaddr
from typing import Any

from googleapiclient.errors import HttpError

from aeca.adapters.google_errors import classify, execute, execute_unless_missing
from aeca.application.ports import MessageSummary
from aeca.domain.email import RawEmail
from aeca.domain.outgoing import OutgoingMessage, SentMessage

# Day-1 spike: confirm this finds exactly the unprocessed Inbox mail. Gmail search writes the "/"
# in the label AECA/processed as "-".
INBOX_QUERY = "in:inbox -label:AECA-processed"
LIST_PAGE_SIZE = 500
SYSTEM_LABELS = frozenset({"INBOX", "SPAM", "STARRED", "UNREAD", "TRASH", "IMPORTANT", "SENT"})
SUMMARY_HEADERS = ["From", "X-AECA-Kind", "X-AECA-Id"]
LABEL_ALREADY_EXISTS = 409
USER = "me"


class GmailMailbox:
    def __init__(self, service: Any, account: str) -> None:
        self._messages = service.users().messages()
        self._threads = service.users().threads()
        self._drafts = service.users().drafts()
        self._labels = service.users().labels()
        self._profile = service.users()
        self._account = account
        self._label_ids: dict[str, str] | None = None

    def address(self) -> str:
        profile = execute(self._profile.getProfile(userId=USER))
        return str(profile["emailAddress"])

    def unprocessed_inbox(self, limit: int) -> list[str]:
        newest_first = [entry["id"] for page in self._inbox_pages() for entry in page]
        return newest_first[::-1][:limit]

    def get(self, message_id: str) -> RawEmail:
        reply = execute(self._messages.get(userId=USER, id=message_id, format="raw"))
        return RawEmail(
            message_id=message_id,
            thread_id=str(reply["threadId"]),
            label_ids=frozenset(reply.get("labelIds", [])),
            rfc822=_decode_base64url(str(reply["raw"])),
            internal_date=_from_epoch_millis(str(reply["internalDate"])),
        )

    def thread_summaries(self, thread_id: str) -> list[MessageSummary]:
        thread = execute(
            self._threads.get(
                userId=USER, id=thread_id, format="metadata", metadataHeaders=SUMMARY_HEADERS
            )
        )
        messages = sorted(thread["messages"], key=lambda m: int(m["internalDate"]))
        return [_summary_of(message) for message in messages]

    def ensure_labels(self, names: Sequence[str]) -> None:
        for name in names:
            self._id_of(name)

    def modify(self, message_id: str, add: Sequence[str], remove: Sequence[str]) -> None:
        if not add and not remove:
            return
        body = {
            "addLabelIds": [self._id_of(name) for name in add],
            "removeLabelIds": [self._id_of(name) for name in remove],
        }
        execute(self._messages.modify(userId=USER, id=message_id, body=body))

    def create_draft(self, message: OutgoingMessage) -> str:
        draft_message: dict[str, str] = {"raw": _encode_base64url(self._mime_of(message))}
        if message.thread_id:
            draft_message["threadId"] = message.thread_id
        reply = execute(self._drafts.create(userId=USER, body={"message": draft_message}))
        return str(reply["id"])

    def draft_exists(self, draft_id: str) -> bool:
        found = execute_unless_missing(self._drafts.get(userId=USER, id=draft_id, format="minimal"))
        return found is not None

    def send_draft(self, draft_id: str) -> SentMessage:
        reply = execute(self._drafts.send(userId=USER, body={"id": draft_id}))
        return SentMessage(message_id=str(reply["id"]), thread_id=str(reply["threadId"]))

    def _inbox_pages(self) -> Any:
        page_token: str | None = None
        while True:
            request = self._messages.list(
                userId=USER, q=INBOX_QUERY, maxResults=LIST_PAGE_SIZE, **_page(page_token)
            )
            reply = execute(request)
            yield reply.get("messages", [])
            page_token = reply.get("nextPageToken")
            if not page_token:
                return

    def _id_of(self, name: str) -> str:
        if name in SYSTEM_LABELS:
            return name
        known = self._known_labels()
        if name not in known:
            known[name] = self._create_label(name)
        return known[name]

    def _known_labels(self) -> dict[str, str]:
        if self._label_ids is None:
            self._label_ids = self._read_labels()
        return self._label_ids

    def _read_labels(self) -> dict[str, str]:
        listed = execute(self._labels.list(userId=USER))
        return {label["name"]: label["id"] for label in listed.get("labels", [])}

    def _create_label(self, name: str) -> str:
        body = {"name": name, "labelListVisibility": "labelShow", "messageListVisibility": "show"}
        try:
            return str(self._labels.create(userId=USER, body=body).execute()["id"])
        except HttpError as error:
            if error.resp.status != LABEL_ALREADY_EXISTS:
                raise classify(error) from error
            # Another process made it after our list, so the cache is stale.
            return self._read_labels()[name]
        except Exception as error:
            raise classify(error) from error

    def _mime_of(self, message: OutgoingMessage) -> bytes:
        mime = EmailMessage(policy=policy.SMTP)
        mime["From"] = self._account
        mime["To"] = message.to
        mime["Subject"] = " ".join(message.subject.split())
        mime["X-AECA-Kind"] = message.kind
        mime["X-AECA-Id"] = message.aeca_id
        _add_threading_headers(mime, message)
        # Quoted-printable keeps every line 7-bit, which any relay accepts.
        mime.set_content(message.body, charset="utf-8", cte="quoted-printable")
        return mime.as_bytes()


def _add_threading_headers(mime: EmailMessage, message: OutgoingMessage) -> None:
    if message.in_reply_to is None:
        mime["Auto-Submitted"] = "auto-generated"
        return
    mime["Auto-Submitted"] = "auto-replied"
    mime["In-Reply-To"] = message.in_reply_to
    mime["References"] = message.references or message.in_reply_to


def _page(page_token: str | None) -> dict[str, str]:
    return {"pageToken": page_token} if page_token else {}


def _summary_of(message: Any) -> MessageSummary:
    headers = {header["name"].lower(): header["value"] for header in message["payload"]["headers"]}
    return MessageSummary(
        message_id=message["id"],
        thread_id=message["threadId"],
        label_ids=frozenset(message.get("labelIds", [])),
        internal_date=_from_epoch_millis(message["internalDate"]),
        sender=parseaddr(headers.get("from", ""))[1].lower(),
        aeca_kind=headers.get("x-aeca-kind"),
        aeca_id=headers.get("x-aeca-id"),
    )


def _from_epoch_millis(millis: str) -> datetime:
    return datetime.fromtimestamp(int(millis) / 1000, tz=UTC)


def _decode_base64url(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _encode_base64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii")
