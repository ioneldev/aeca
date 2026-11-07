"""The polling loop: one tick reads the answers to approval requests first, then the inbox, and
a system-wide error makes the whole service back off instead of failing any email."""

import threading
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from aeca.application.failures import ends_the_tick
from aeca.application.ports import Mailbox
from aeca.application.watch import FailureWatch
from aeca.domain.audit import AuditLine, Outcome
from aeca.domain.errors import AecaError, EmailError
from aeca.domain.labels import ALL_USER_LABELS

BACKOFF_SECONDS = (30, 60, 120, 300)
# Emails handled per tick. A skipped one costs no model call and does not count.
INBOX_MESSAGES_PER_TICK = 50
# Ids read per tick: enough that answers waiting for the approval loop, skipped on every tick,
# never crowd a new email out of the listing.
INBOX_IDS_READ_PER_TICK = 500


class EmailProcessor(Protocol):
    def process(self, message_id: str) -> AuditLine | None: ...

    def unfinished(self) -> list[str]:
        """Emails to resume by id, which the inbox listing may no longer return."""
        ...


class ApprovalLoop(Protocol):
    def process_all(self) -> list[AuditLine]:
        """An audit line for each approval outcome; raises a system-wide error as it is."""
        ...


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
    approvals: ApprovalLoop
    watch: FailureWatch
    # The checks and the resume that must pass before the first poll; they raise AecaError.
    start_up: Callable[[], None]


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
        self._deps.start_up()
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
            self._read_approvals()
            for message_id in self._emails_to_handle():
                if self._stop.is_set() or _handled(results) == INBOX_MESSAGES_PER_TICK:
                    break
                results.append(self._process(message_id))
        except Exception as error:
            if not ends_the_tick(error):
                raise
            assert isinstance(error, AecaError)
            self._deps.watch.tick_ended_by(error)
            return self._backing_off(results, error)
        self._outages_in_a_row = 0
        self._deps.watch.tick_completed()
        return _tick_result(results, backoff_s=0)

    # Spec §4.6: the approval loop runs first, so an answer is read before the inbox loop could
    # see its thread, and a closed approval's holds are free before new mail is planned.
    def _read_approvals(self) -> None:
        try:
            lines = self._deps.approvals.process_all()
        except Exception as error:
            if ends_the_tick(error):
                raise
            self._deps.console.event("approvals_failed", error=str(error))
            return
        for line in lines:
            self._deps.console.email(line)

    def _emails_to_handle(self) -> Iterator[str]:
        resumed = self._deps.processor.unfinished()
        yield from resumed
        for message_id in self._deps.mailbox.unprocessed_inbox(INBOX_IDS_READ_PER_TICK):
            if message_id not in resumed:
                yield message_id

    def _backing_off(self, results: list[_Result], error: AecaError) -> TickResult:
        return _tick_result(results, backoff_s=self._back_off(error))

    def _process(self, message_id: str) -> _Result:
        try:
            line = self._deps.processor.process(message_id)
        except EmailError as error:
            self._deps.console.event("email_failed", message_id=message_id, error=str(error))
            self._deps.watch.email_failed(message_id, str(error))
            return _Result.FAILED
        if line is None:
            return _Result.SKIPPED
        self._deps.console.email(line)
        if line.outcome == Outcome.FAILED:
            self._deps.watch.email_failed(message_id, line.error or "no error was recorded")
            return _Result.FAILED
        self._deps.watch.email_processed(used_model=line.usd > 0)
        return _Result.PROCESSED

    def _back_off(self, error: AecaError) -> int:
        self._outages_in_a_row += 1
        seconds = BACKOFF_SECONDS[min(self._outages_in_a_row, len(BACKOFF_SECONDS)) - 1]
        self._deps.console.event(
            "backoff", seconds=seconds, reason=str(error), next_action=error.next_action
        )
        return seconds


def _handled(results: list[_Result]) -> int:
    return len(results) - results.count(_Result.SKIPPED)


def _tick_result(results: list[_Result], *, backoff_s: int) -> TickResult:
    return TickResult(
        processed=results.count(_Result.PROCESSED),
        failed=results.count(_Result.FAILED),
        backoff_s=backoff_s,
    )
