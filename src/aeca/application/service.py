"""The polling loop: one tick reads the inbox and processes each email, and a system-wide error
makes the whole service back off instead of failing any email."""

import threading
from dataclasses import dataclass
from typing import Protocol

from aeca.application.decide import BudgetExhausted
from aeca.application.ports import Mailbox, ProviderError
from aeca.domain.audit import AuditLine
from aeca.domain.errors import AecaError, EmailError, SystemWideError
from aeca.domain.labels import ALL_USER_LABELS

BACKOFF_SECONDS = (30, 60, 120, 300)
INBOX_MESSAGES_PER_TICK = 50
FAILED = "failed"


class EmailProcessor(Protocol):
    def process(self, message_id: str) -> AuditLine | None: ...


class Console(Protocol):
    """What the service says on stdout, as JSON lines."""

    def email(self, line: AuditLine) -> None: ...

    def event(self, kind: str, **fields: object) -> None: ...


@dataclass(frozen=True)
class ServiceDeps:
    processor: EmailProcessor
    mailbox: Mailbox
    console: Console
    poll_seconds: float


@dataclass(frozen=True, slots=True)
class TickResult:
    processed: int
    failed: int
    backoff_s: int


class Service:
    def __init__(self, deps: ServiceDeps) -> None:
        self._deps = deps
        self._outages_in_a_row = 0

    def run(self, stop: threading.Event) -> None:
        self._deps.mailbox.ensure_labels(ALL_USER_LABELS)
        self._deps.console.event("service_started", poll_seconds=self._deps.poll_seconds)
        while not stop.is_set():
            result = self.tick(stop)
            # Waiting on the event, not sleeping, so a stop does not wait out the poll.
            stop.wait(result.backoff_s or self._deps.poll_seconds)
        self._deps.console.event("service_stopped")

    def tick(self, stop: threading.Event | None = None) -> TickResult:
        processed = failed = 0
        try:
            for message_id in self._deps.mailbox.unprocessed_inbox(INBOX_MESSAGES_PER_TICK):
                if stop is not None and stop.is_set():
                    break
                outcome = self._process(message_id)
                processed += outcome == "processed"
                failed += outcome == FAILED
        except (SystemWideError, BudgetExhausted, ProviderError) as error:
            if not _ends_the_tick(error):
                raise
            return TickResult(processed, failed, self._back_off(error))
        self._outages_in_a_row = 0
        return TickResult(processed, failed, 0)

    def _process(self, message_id: str) -> str:
        try:
            line = self._deps.processor.process(message_id)
        except EmailError:
            return FAILED
        if line is None:
            return "skipped"
        self._deps.console.email(line)
        return FAILED if line.outcome == FAILED else "processed"

    def _back_off(self, error: AecaError) -> int:
        self._outages_in_a_row += 1
        seconds = BACKOFF_SECONDS[min(self._outages_in_a_row, len(BACKOFF_SECONDS)) - 1]
        self._deps.console.event(
            "backoff", seconds=seconds, reason=str(error), next_action=error.next_action
        )
        return seconds


def _ends_the_tick(error: AecaError) -> bool:
    if isinstance(error, ProviderError):
        return error.system_wide
    return True
