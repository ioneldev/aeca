"""A mailbox held in memory, so a test sees exactly which labels were added and in what order."""

from collections.abc import Sequence
from dataclasses import dataclass, replace

from aeca.application.ports import MessageSummary
from aeca.domain.email import RawEmail
from aeca.domain.outgoing import OutgoingMessage, SentMessage

INBOX = "INBOX"
PROCESSED = "AECA/processed"


@dataclass(frozen=True, slots=True)
class ModifyCall:
    message_id: str
    add: tuple[str, ...]
    remove: tuple[str, ...]


class FakeMailbox:
    def __init__(self, address: str = "aeca@gmail.com") -> None:
        self._address = address
        self._messages: dict[str, RawEmail] = {}
        self.modify_log: list[ModifyCall] = []
        self.ensured_labels: list[str] = []
        self.get_log: list[str] = []
        # Failures to raise, by message id, from get().
        self.get_failures: dict[str, Exception] = {}
        # What unprocessed_inbox returns instead of the messages themselves: a stale listing.
        self.listing_override: list[str] | None = None
        self.listing_failures: list[Exception] = []
        self.listing_calls: list[int] = []

    def add(self, raw: RawEmail) -> None:
        self._messages[raw.message_id] = raw

    def labels_of(self, message_id: str) -> frozenset[str]:
        return self._messages[message_id].label_ids

    def added_labels(self, message_id: str) -> list[str]:
        return [
            label for call in self.modify_log if call.message_id == message_id for label in call.add
        ]

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
        return self._messages[message_id]

    def thread_summaries(self, thread_id: str) -> list[MessageSummary]:
        raise NotImplementedError

    def ensure_labels(self, names: Sequence[str]) -> None:
        self.ensured_labels.extend(names)

    def modify(self, message_id: str, add: Sequence[str], remove: Sequence[str]) -> None:
        self.modify_log.append(ModifyCall(message_id, tuple(add), tuple(remove)))
        message = self._messages.get(message_id)
        if message is None:
            return
        labels = (message.label_ids | set(add)) - set(remove)
        self._messages[message_id] = replace(message, label_ids=frozenset(labels))

    def create_draft(self, message: OutgoingMessage) -> str:
        raise NotImplementedError

    def draft_exists(self, draft_id: str) -> bool:
        raise NotImplementedError

    def send_draft(self, draft_id: str) -> SentMessage:
        raise NotImplementedError
