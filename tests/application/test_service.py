import threading
from decimal import Decimal

import pytest
from fakes.fake_mailbox import FakeMailbox
from fakes.recording_console import RecordingConsole
from support.audit_lines import line_for
from support.quiet_service import NoCopyLock, NoHeartbeat, nothing_to_start, quiet_watch

from aeca.application.decide import BudgetExhausted
from aeca.application.ports import ProviderError
from aeca.application.service import (
    BACKOFF_SECONDS,
    INBOX_MESSAGES_PER_TICK,
    Service,
    ServiceDeps,
)
from aeca.domain.audit import AuditLine
from aeca.domain.errors import CredentialsRevoked, EmailError
from aeca.domain.labels import ERROR, PROCESSED

POLL_SECONDS = 0.01


class ScriptedProcessor:
    """Each email id maps to a line, None (skipped) or an exception to raise."""

    def __init__(self) -> None:
        self.script: dict[str, AuditLine | Exception | None] = {}
        self.seen: list[str] = []
        self.unfinished_ids: list[str] = []

    def unfinished(self) -> list[str]:
        return list(self.unfinished_ids)

    def process(self, message_id: str) -> AuditLine | None:
        self.seen.append(message_id)
        outcome = self.script.get(message_id, line_for(message_id))
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class ScriptedApprovals:
    """The approval loop: each tick returns the scripted lines, or raises the scripted error."""

    def __init__(self, order: list[str]) -> None:
        self.lines: list[AuditLine] = []
        self.failure: Exception | None = None
        self._order = order

    def process_all(self) -> list[AuditLine]:
        self._order.append("approvals")
        if self.failure is not None:
            raise self.failure
        return list(self.lines)


class Rig:
    def __init__(self) -> None:
        self.mailbox = FakeMailbox()
        self.processor = ScriptedProcessor()
        self.console = RecordingConsole()
        self.order: list[str] = []
        self.approvals = ScriptedApprovals(self.order)
        self.service = Service(
            ServiceDeps(
                processor=self.processor,
                mailbox=self.mailbox,
                console=self.console,
                poll_seconds=POLL_SECONDS,
                approvals=self.approvals,
                watch=quiet_watch(),
                start_up=nothing_to_start,
                copy_lock=NoCopyLock(),
                heartbeat=NoHeartbeat(),
            )
        )

    def inbox(self, *ids: str) -> None:
        self.mailbox.listing_override = list(ids)


@pytest.fixture
def rig() -> Rig:
    return Rig()


def test_a_tick_processes_every_listed_email_and_prints_each_line(rig: Rig) -> None:
    rig.inbox("a", "b")

    result = rig.service.tick()

    assert (result.processed, result.failed, result.backoff_s) == (2, 0, 0)
    assert [line.message_id for line in rig.console.emails] == ["a", "b"]


def test_a_tick_handles_at_most_fifty_emails(rig: Rig) -> None:
    rig.inbox(*(f"m{number}" for number in range(60)))

    result = rig.service.tick()

    assert INBOX_MESSAGES_PER_TICK == 50
    assert result.processed == 50
    assert rig.processor.seen == [f"m{number}" for number in range(50)]


def test_skipped_emails_do_not_count_toward_the_fifty(rig: Rig) -> None:
    skipped = [f"answer{number}" for number in range(60)]
    rig.inbox(*skipped, "new")
    for message_id in skipped:
        rig.processor.script[message_id] = None

    result = rig.service.tick()

    assert result.processed == 1
    assert rig.processor.seen[-1] == "new"


def test_unfinished_work_comes_first_and_is_not_processed_twice(rig: Rig) -> None:
    rig.inbox("listed", "archived-but-unfinished")
    rig.processor.unfinished_ids = ["archived-but-unfinished"]

    rig.service.tick()

    assert rig.processor.seen == ["archived-but-unfinished", "listed"]


def test_a_skipped_email_is_neither_processed_nor_failed(rig: Rig) -> None:
    rig.inbox("a")
    rig.processor.script["a"] = None

    result = rig.service.tick()

    assert (result.processed, result.failed) == (0, 0)
    assert rig.console.emails == []


def test_one_failing_email_does_not_stop_the_next(rig: Rig) -> None:
    rig.inbox("bad", "good")
    rig.processor.script["bad"] = line_for("bad", outcome="failed")

    result = rig.service.tick()

    assert rig.processor.seen == ["bad", "good"]
    assert (result.processed, result.failed) == (1, 1)


def test_an_email_error_that_escapes_the_processor_still_does_not_stop_the_tick(rig: Rig) -> None:
    rig.inbox("bad", "good")
    rig.processor.script["bad"] = EmailError("label refused", next_action="none")

    result = rig.service.tick()

    assert rig.processor.seen == ["bad", "good"]
    assert (result.processed, result.failed) == (1, 1)


@pytest.mark.parametrize(
    "error",
    [
        CredentialsRevoked("revoked", next_action="make auth"),
        ProviderError("down", next_action="wait", kind="overloaded", system_wide=True),
        BudgetExhausted(spent_usd=Decimal(5)),
    ],
    ids=["credentials", "provider-outage", "budget"],
)
def test_a_system_wide_error_ends_the_tick_and_charges_no_email(rig: Rig, error: Exception) -> None:
    rig.inbox("a", "b", "c")
    rig.processor.script["b"] = error

    result = rig.service.tick()

    assert rig.processor.seen == ["a", "b"]
    assert (result.processed, result.failed, result.backoff_s) == (1, 0, BACKOFF_SECONDS[0])
    assert rig.console.kinds() == ["backoff"]
    assert rig.console.events[0].fields["seconds"] == 30


def test_a_system_wide_error_while_listing_backs_off_too(rig: Rig) -> None:
    rig.mailbox.listing_failures.append(CredentialsRevoked("revoked", next_action="make auth"))

    result = rig.service.tick()

    assert (result.processed, result.failed, result.backoff_s) == (0, 0, 30)


def test_backoff_grows_and_resets_after_a_good_tick(rig: Rig) -> None:
    rig.inbox("a")
    rig.processor.script["a"] = CredentialsRevoked("revoked", next_action="make auth")

    grown = [rig.service.tick().backoff_s for _ in range(5)]
    rig.processor.script.clear()
    after_good_tick = rig.service.tick().backoff_s
    rig.processor.script["a"] = CredentialsRevoked("revoked", next_action="make auth")
    after_reset = rig.service.tick().backoff_s

    assert BACKOFF_SECONDS == (30, 60, 120, 300)
    assert grown == [30, 60, 120, 300, 300]
    assert after_good_tick == 0
    assert after_reset == 30


def test_run_makes_the_labels_then_ticks_until_stopped_then_says_so(rig: Rig) -> None:
    stop = threading.Event()
    rig.inbox("a")
    original = rig.processor.process

    def stopping_after_the_first_email(message_id: str) -> AuditLine | None:
        stop.set()
        return original(message_id)

    rig.processor.process = stopping_after_the_first_email  # type: ignore[method-assign]

    rig.service.run(stop)

    assert PROCESSED in rig.mailbox.ensured_labels
    assert ERROR in rig.mailbox.ensured_labels
    assert "SPAM" not in rig.mailbox.ensured_labels
    assert rig.console.kinds() == ["service_started", "service_stopped"]
    assert [line.message_id for line in rig.console.emails] == ["a"]


def test_a_stop_finishes_the_email_in_hand_and_starts_no_other(rig: Rig) -> None:
    stop = threading.Event()
    rig.inbox("a", "b")
    original = rig.processor.process

    def stopping_during_the_first_email(message_id: str) -> AuditLine | None:
        stop.set()
        return original(message_id)

    rig.processor.process = stopping_during_the_first_email  # type: ignore[method-assign]

    rig.service.run(stop)

    assert rig.processor.seen == ["a"]
    assert [line.message_id for line in rig.console.emails] == ["a"]


def test_an_email_error_that_escapes_the_processor_is_printed_as_an_event(rig: Rig) -> None:
    rig.inbox("bad")
    rig.processor.script["bad"] = EmailError("label refused", next_action="none")

    rig.service.tick()

    assert rig.console.kinds() == ["email_failed"]
    assert rig.console.events[0].fields["message_id"] == "bad"
    assert "label refused" in str(rig.console.events[0].fields["error"])


def test_run_waits_on_the_stop_event_so_a_stop_is_prompt(rig: Rig) -> None:
    stop = threading.Event()
    stop.set()
    slow = Service(
        ServiceDeps(
            processor=rig.processor,
            mailbox=rig.mailbox,
            console=rig.console,
            poll_seconds=3600,
            approvals=rig.approvals,
            watch=quiet_watch(),
            start_up=nothing_to_start,
            copy_lock=NoCopyLock(),
            heartbeat=NoHeartbeat(),
        )
    )

    slow.run(stop)

    assert rig.console.kinds() == ["service_started", "service_stopped"]


def test_run_waits_the_backoff_not_the_poll_after_an_outage(rig: Rig) -> None:
    waits: list[float] = []

    class RecordingEvent(threading.Event):
        def wait(self, timeout: float | None = None) -> bool:
            assert timeout is not None
            waits.append(timeout)
            self.set()
            return True

    rig.mailbox.listing_failures.append(CredentialsRevoked("revoked", next_action="make auth"))

    rig.service.run(RecordingEvent())

    assert waits == [30]


# --- the approval loop runs first (spec §4.6) ---


def test_the_approval_loop_runs_before_the_inbox_loop(rig: Rig) -> None:
    rig.inbox("a")
    original = rig.processor.process

    def noting_the_order(message_id: str) -> AuditLine | None:
        rig.order.append(message_id)
        return original(message_id)

    rig.processor.process = noting_the_order  # type: ignore[method-assign]

    rig.service.tick()

    assert rig.order == ["approvals", "a"]


def test_each_approval_outcome_is_printed_and_not_counted_as_an_email(rig: Rig) -> None:
    rig.approvals.lines = [line_for("answer-1"), line_for("answer-2", outcome="ignored")]

    result = rig.service.tick()

    assert [line.message_id for line in rig.console.emails] == ["answer-1", "answer-2"]
    assert (result.processed, result.failed) == (0, 0)


def test_a_system_wide_error_in_the_approval_loop_ends_the_tick_before_the_inbox(rig: Rig) -> None:
    rig.inbox("a")
    rig.approvals.failure = CredentialsRevoked("revoked", next_action="make auth")

    result = rig.service.tick()

    assert rig.processor.seen == []
    assert result.backoff_s == BACKOFF_SECONDS[0]
    assert rig.console.kinds() == ["backoff"]


def test_any_other_failure_of_the_approval_loop_is_printed_and_the_inbox_still_runs(
    rig: Rig,
) -> None:
    rig.inbox("a")
    rig.approvals.failure = EmailError("thread unreadable", next_action="none")

    result = rig.service.tick()

    assert rig.processor.seen == ["a"]
    assert result.processed == 1
    assert rig.console.kinds() == ["approvals_failed"]
    assert "thread unreadable" in str(rig.console.events[0].fields["error"])
