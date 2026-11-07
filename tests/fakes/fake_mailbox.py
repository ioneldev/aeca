"""A mailbox held in memory, so a test sees exactly which labels were added and in what order.

Sent mail joins the mailbox as Gmail's does: in its thread, with SENT and AECA's headers, and with
INBOX once its conversation holds inbox mail (day-1 spike). A test can kill AECA right after any
write, to see what a restart makes of it.
"""

from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from email import message_from_bytes, policy
from email.message import EmailMessage
from email.utils import parseaddr

from fakes.crash import Killed
from support.emails import make_raw

from aeca.application.ports import MessageSummary
from aeca.domain.email import RawEmail
from aeca.domain.errors import EmailError
from aeca.domain.outgoing import OutgoingMessage, SentMessage

INBOX = "INBOX"
SENT = "SENT"
PROCESSED = "AECA/processed"
DEFAULT_SENT_AT = datetime(2026, 11, 7, 9, 0, tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class ModifyCall:
    message_id: str
    add: tuple[str, ...]
    remove: tuple[str, ...]


class FakeMailbox:
    def __init__(
        self, address: str = "aeca@gmail.com", *, journal: list[str] | None = None
    ) -> None:
        self._address = address
        # Shared with other fakes, so a test can assert the order across them.
        self._journal = journal if journal is not None else []
        self._messages: dict[str, RawEmail] = {}
        self.modify_log: list[ModifyCall] = []
        self.ensured_labels: list[str] = []
        self.get_log: list[str] = []
        # Failures to raise, by message id, from get().
        self.get_failures: dict[str, Exception] = {}
        # Failures raised by the next calls to modify(), in order, before the change applies.
        self.modify_failures: list[Exception] = []
        # Failures raised by the next calls to send_draft(), in order, before anything is sent.
        self.send_failures: list[Exception] = []
        # What unprocessed_inbox returns instead of the messages themselves: a stale listing.
        self.listing_override: list[str] | None = None
        self.listing_failures: list[Exception] = []
        self.listing_calls: list[int] = []
        self.drafts: dict[str, OutgoingMessage] = {}
        self.sent: list[OutgoingMessage] = []
        self.thread_reads: list[str] = []
        # Raise Killed right after this many more writes (modify, create_draft, send_draft).
        self.kill_after_writes: int | None = None
        # Raise Killed right after the first write whose journal entry starts with this.
        self.kill_after_entry: str | None = None
        self._ids_issued = 0

    def add(self, raw: RawEmail) -> None:
        self._messages[raw.message_id] = raw

    def labels_of(self, message_id: str) -> frozenset[str]:
        return self._messages[message_id].label_ids

    def added_labels(self, message_id: str) -> list[str]:
        return [
            label for call in self.modify_log if call.message_id == message_id for label in call.add
        ]

    def sent_to(self, address: str) -> list[OutgoingMessage]:
        return [message for message in self.sent if message.to == address]

    def address(self) -> str:
        return self._address

    def unprocessed_inbox(self, limit: int) -> list[str]:
        self.listing_calls.append(limit)
        if self.listing_failures:
            raise self.listing_failures.pop(0)
        if self.listing_override is not None:
            return self.listing_override[:limit]
        waiting = [
            message.message_id
            for message in self._messages.values()
            if INBOX in message.label_ids and PROCESSED not in message.label_ids
        ]
        return waiting[:limit]

    def get(self, message_id: str) -> RawEmail:
        self.get_log.append(message_id)
        if message_id in self.get_failures:
            raise self.get_failures[message_id]
        if message_id not in self._messages:
            raise EmailError(f"no message {message_id}", next_action="none")
        return self._messages[message_id]

    def thread_summaries(self, thread_id: str) -> list[MessageSummary]:
        self.thread_reads.append(thread_id)
        in_thread = [raw for raw in self._messages.values() if raw.thread_id == thread_id]
        # sorted is stable, so messages received at the same instant keep their arrival order.
        return [_summary_of(raw) for raw in sorted(in_thread, key=lambda raw: raw.internal_date)]

    def ensure_labels(self, names: Sequence[str]) -> None:
        self.ensured_labels.extend(names)

    def modify(self, message_id: str, add: Sequence[str], remove: Sequence[str]) -> None:
        self.modify_log.append(ModifyCall(message_id, tuple(add), tuple(remove)))
        if self.modify_failures:
            raise self.modify_failures.pop(0)
        message = self._messages.get(message_id)
        if message is None:
            raise EmailError(f"no message {message_id}", next_action="none")
        entry = "modify:" + ",".join(add) + _removed(remove)
        self._journal.append(entry)
        labels = (message.label_ids | set(add)) - set(remove)
        self._messages[message_id] = replace(message, label_ids=frozenset(labels))
        self._after_write(entry)

    def create_draft(self, message: OutgoingMessage) -> str:
        draft_id = self._issue_id("draft")
        self.drafts[draft_id] = message
        self._journal.append(f"draft:{message.kind}")
        self._after_write(f"draft:{message.kind}")
        return draft_id

    def draft_exists(self, draft_id: str) -> bool:
        return draft_id in self.drafts

    def send_draft(self, draft_id: str) -> SentMessage:
        if self.send_failures:
            raise self.send_failures.pop(0)
        if draft_id not in self.drafts:
            raise EmailError(f"no draft {draft_id}", next_action="none")
        message = self.drafts.pop(draft_id)
        sent = self._deliver(message)
        self.sent.append(message)
        self._journal.append(f"send:{message.kind}")
        self._after_write(f"send:{message.kind}")
        return SentMessage(message_id=sent.message_id, thread_id=sent.thread_id)

    def _deliver(self, message: OutgoingMessage) -> RawEmail:
        message_id = self._issue_id("sent")
        thread_id = message.thread_id or f"thread-of-{message_id}"
        raw = make_raw(
            sender=self._address,
            to=message.to,
            subject=message.subject,
            body=message.body,
            headers={"X-AECA-Kind": message.kind, "X-AECA-Id": message.aeca_id},
            label_ids=self._labels_of_sent(thread_id, message.to),
            message_id=message_id,
            thread_id=thread_id,
            internal_date=self._latest_in(thread_id) + timedelta(seconds=1),
        )
        self.add(raw)
        return raw

    def _labels_of_sent(self, thread_id: str, to: str) -> frozenset[str]:
        thread_in_inbox = any(
            INBOX in raw.label_ids for raw in self._messages.values() if raw.thread_id == thread_id
        )
        to_itself = to.lower() == self._address.lower()
        return frozenset({SENT, INBOX}) if thread_in_inbox or to_itself else frozenset({SENT})

    def _latest_in(self, thread_id: str) -> datetime:
        dates = [raw.internal_date for raw in self._messages.values() if raw.thread_id == thread_id]
        return max(dates, default=DEFAULT_SENT_AT)

    def _issue_id(self, prefix: str) -> str:
        self._ids_issued += 1
        return f"{prefix}-{self._ids_issued}"

    def _after_write(self, entry: str) -> None:
        if self.kill_after_entry is not None and entry.startswith(self.kill_after_entry):
            self.kill_after_entry = None
            raise Killed
        if self.kill_after_writes is None:
            return
        self.kill_after_writes -= 1
        if self.kill_after_writes == 0:
            self.kill_after_writes = None
            raise Killed


def _removed(remove: Sequence[str]) -> str:
    return "-" + ",".join(remove) if remove else ""


def _summary_of(raw: RawEmail) -> MessageSummary:
    headers = message_from_bytes(raw.rfc822, policy=policy.default)
    assert isinstance(headers, EmailMessage)
    return MessageSummary(
        message_id=raw.message_id,
        thread_id=raw.thread_id,
        label_ids=raw.label_ids,
        internal_date=raw.internal_date,
        sender=parseaddr(str(headers["From"] or ""))[1].lower(),
        aeca_kind=_header(headers, "X-AECA-Kind"),
        aeca_id=_header(headers, "X-AECA-Id"),
    )


def _header(headers: EmailMessage, name: str) -> str | None:
    value = headers[name]
    return None if value is None else str(value)
