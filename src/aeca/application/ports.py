"""What the application needs from the outside world. Adapters implement these."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Protocol

from pydantic import BaseModel

from aeca.domain.calendar_event import CalendarEvent, NewEvent
from aeca.domain.email import RawEmail
from aeca.domain.errors import AecaError
from aeca.domain.outgoing import OutgoingMessage, SentMessage
from aeca.domain.time_range import Interval

DEFAULT_MAX_TOKENS = 16000


class Clock(Protocol):
    def now(self) -> datetime:
        """The current time, aware and in UTC."""
        ...


class Tracer(Protocol):
    def event(self, kind: str, **fields: object) -> None: ...

    def debug(self, text: str) -> None: ...


@dataclass(frozen=True, slots=True)
class Usage:
    input_tokens: int
    output_tokens: int
    cache_read: int
    cache_write: int


@dataclass(frozen=True, slots=True)
class PaidCall:
    """What one call cost. A call that failed afterwards was still paid for."""

    usage: Usage
    model: str
    cost_usd: Decimal
    latency_s: float


@dataclass(frozen=True, slots=True)
class LLMResponse(PaidCall):
    # Sent back unchanged as the assistant turn of a correction, because the API checks thinking
    # blocks byte for byte.
    content: tuple[object, ...]
    request_id: str | None


@dataclass(frozen=True, slots=True)
class LLMRequest:
    model: str
    effort: str
    system: str
    messages: tuple[dict[str, object], ...]
    max_tokens: int = DEFAULT_MAX_TOKENS


class ProviderError(AecaError):
    """The model provider failed.

    system_wide says that every email would fail the same way, so the whole tick stops. paid_call
    holds what the API reported before the failure, for the caller to charge.
    """

    def __init__(
        self,
        message: str,
        *,
        next_action: str,
        kind: str,
        system_wide: bool,
        paid_call: PaidCall | None = None,
    ) -> None:
        super().__init__(message, next_action=next_action)
        self.kind = kind
        self.system_wide = system_wide
        self.paid_call = paid_call


class ReplyUnusable(AecaError):
    """The reply came back whole, and was paid for, but holds no decision that fits the schema."""

    def __init__(self, problem: str, *, response: LLMResponse) -> None:
        super().__init__(
            f"The model's reply is unusable: {problem}",
            next_action="Send the model a correction, or give the email the fallback decision",
        )
        self.problem = problem
        self.response = response


class LLMClient(Protocol):
    def parse[T: BaseModel](self, request: LLMRequest, schema: type[T]) -> tuple[T, LLMResponse]:
        """Raises ProviderError when the call fails or is refused, and ReplyUnusable when the
        reply does not fit the schema."""
        ...


@dataclass(frozen=True, slots=True)
class MessageSummary:
    """One message of a thread, read from its headers alone. A thread cannot be read raw."""

    message_id: str
    thread_id: str
    label_ids: frozenset[str]
    internal_date: datetime
    sender: str
    aeca_kind: str | None
    aeca_id: str | None


class Mailbox(Protocol):
    """The one Gmail account. Every change applies to one message, never to a thread.

    Raises SystemWideError, CredentialsRevoked or EmailError, never a Google exception.
    """

    def address(self) -> str: ...

    def unprocessed_inbox(self, limit: int) -> list[str]:
        """Ids of Inbox messages not yet labelled AECA/processed, oldest first."""
        ...

    def get(self, message_id: str) -> RawEmail: ...

    def thread_summaries(self, thread_id: str) -> list[MessageSummary]:
        """Every message of the thread, oldest first."""
        ...

    def ensure_labels(self, names: Sequence[str]) -> None: ...

    def modify(self, message_id: str, add: Sequence[str], remove: Sequence[str]) -> None: ...

    def create_draft(self, message: OutgoingMessage) -> str:
        """The draft's id."""
        ...

    def draft_exists(self, draft_id: str) -> bool: ...

    def send_draft(self, draft_id: str) -> SentMessage: ...


class Calendar(Protocol):
    """The account's primary calendar. Raises the same errors as Mailbox."""

    def time_zone(self) -> str: ...

    def events(self, window: Interval) -> list[CalendarEvent]:
        """Single events in the window, recurring ones expanded, cancelled ones left out."""
        ...

    def get_event(self, event_id: str) -> CalendarEvent | None:
        """None when no such event exists. A cancelled event comes back with cancelled=True."""
        ...

    def event_by_ical_uid(self, uid: str) -> CalendarEvent | None: ...

    def insert_event(self, event: NewEvent) -> None:
        """Creates an event with no attendees, so Google sends no invitation."""
        ...
