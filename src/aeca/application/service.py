"""The polling loop: one tick reads the inbox and processes each email, and a system-wide error
makes the whole service back off instead of failing any email."""

import threading
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from aeca.application.ports import Mailbox
from aeca.application.process_email import ends_the_tick
from aeca.domain.audit import AuditLine, Outcome
from aeca.domain.errors import AecaError, EmailError
from aeca.domain.labels import ALL_USER_LABELS

BACKOFF_SECONDS = (30, 60, 120, 300)
INBOX_MESSAGES_PER_TICK = 50


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


class _Result(StrEnum):
    PROCESSED = "processed"
    FAILED = "failed"
    SKIPPED = "skipped"


class Service:
    def __init__(self, deps: ServiceDeps) -> None:
        self._deps = deps
        self._outages_in_a_row = 0
        self._stop = threading.Event()

    def run(self, stop: threading.Event) -> None:
        self._stop = stop
        self._deps.mailbox.ensure_labels(ALL_USER_LABELS)
        self._deps.console.event("service_started", poll_seconds=self._deps.poll_seconds)
        while not stop.is_set():
            result = self.tick()
            # Waiting on the event, not sleeping, so a stop does not wait out the poll.
            stop.wait(result.backoff_s or self._deps.poll_seconds)
        self._deps.console.event("service_stopped")

    def tick(self) -> TickResult:
        results: list[_Result] = []
        try:
            for message_id in self._deps.mailbox.unprocessed_inbox(INBOX_MESSAGES_PER_TICK):
                if self._stop.is_set():
                    break
                results.append(self._process(message_id))
        except Exception as error:
            if not ends_the_tick(error):
                raise
            assert isinstance(error, AecaError)
            return self._backing_off(results, error)
        self._outages_in_a_row = 0
        return _tick_result(results, backoff_s=0)

    def _backing_off(self, results: list[_Result], error: AecaError) -> TickResult:
        return _tick_result(results, backoff_s=self._back_off(error))

    def _process(self, message_id: str) -> _Result:
        try:
            line = self._deps.processor.process(message_id)
        except EmailError as error:
            self._deps.console.event("email_failed", message_id=message_id, error=str(error))
            return _Result.FAILED
        if line is None:
            return _Result.SKIPPED
        self._deps.console.email(line)
        return _Result.FAILED if line.outcome == Outcome.FAILED else _Result.PROCESSED

    def _back_off(self, error: AecaError) -> int:
        self._outages_in_a_row += 1
        seconds = BACKOFF_SECONDS[min(self._outages_in_a_row, len(BACKOFF_SECONDS)) - 1]
        self._deps.console.event(
            "backoff", seconds=seconds, reason=str(error), next_action=error.next_action
        )
        return seconds


def _tick_result(results: list[_Result], *, backoff_s: int) -> TickResult:
    return TickResult(
        processed=results.count(_Result.PROCESSED),
        failed=results.count(_Result.FAILED),
        backoff_s=backoff_s,
    )
