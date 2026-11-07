from dataclasses import dataclass


@dataclass(frozen=True)
class OutgoingMessage:
    to: str
    subject: str
    body: str
    kind: str
    aeca_id: str
    thread_id: str | None
    in_reply_to: str | None
    references: str | None


@dataclass(frozen=True)
class SentMessage:
    message_id: str
    thread_id: str
