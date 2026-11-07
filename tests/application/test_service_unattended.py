"""Running unattended: the heartbeat, the copy lock and the stop on SIGTERM."""

import threading
from datetime import timedelta

import pytest
from fakes.fake_lease import FakeLease
from fakes.fake_mailbox import FakeMailbox
from fakes.fixed_clock import FIXED_NOW, FixedClock
from fakes.recording_console import RecordingConsole
from support.audit_lines import line_for
from support.quiet_service import nothing_to_start, quiet_watch

from aeca.application.copy_lock import CopyLock, CopyLockDeps
from aeca.application.service import Service, ServiceDeps
from aeca.domain.audit import AuditLine
from aeca.domain.errors import ConfigError, CredentialsRevoked, LockLost
from aeca.domain.lease import LeaseHolding

POLL_SECONDS = 30
THIS_COPY = "copy-this"
OTHER_COPY = "copy-other"
STALE_AFTER = timedelta(seconds=3 * POLL_SECONDS)


class CountingHeartbeat:
    def __init__(self) -> None:
        self.beats = 0

    def beat(self) -> None:
        self.beats += 1


class Processor:
    def __init__(self) -> None:
        self.seen: list[str] = []
        self.on_process: list[object] = []

    def unfinished(self) -> list[str]:
        return []

    def process(self, message_id: str) -> AuditLine | None:
        self.seen.append(message_id)
        for action in self.on_process:
            assert callable(action)
            action()
        return line_for(message_id)


class Approvals:
    def __init__(self) -> None:
        self.runs = 0
        self.failure: Exception | None = None

    def process_all(self) -> list[AuditLine]:
        self.runs += 1
        if self.failure is not None:
            raise self.failure
        return []


def a_lock(lease: FakeLease, *, force: bool = False) -> CopyLock:
    return CopyLock(
        CopyLockDeps(
            lease=lease,
            clock=FixedClock(),
            copy_id=THIS_COPY,
            poll_seconds=POLL_SECONDS,
            force=force,
        )
    )


class Rig:
    def __init__(self, lease: FakeLease | None = None) -> None:
        self.lease = lease or FakeLease(LeaseHolding(THIS_COPY, FIXED_NOW))
        self.mailbox = FakeMailbox()
        self.processor = Processor()
        self.approvals = Approvals()
        self.heartbeat = CountingHeartbeat()
        self.service = Service(
            ServiceDeps(
                processor=self.processor,
                mailbox=self.mailbox,
                console=RecordingConsole(),
                poll_seconds=0.01,
                approvals=self.approvals,
                watch=quiet_watch(),
                start_up=nothing_to_start,
                copy_lock=a_lock(self.lease),
                heartbeat=self.heartbeat,
            )
        )

    def inbox(self, *ids: str) -> None:
        self.mailbox.listing_override = list(ids)


# --- the heartbeat ---


def test_the_heartbeat_is_written_after_a_successful_poll() -> None:
    rig = Rig()
    rig.inbox("a")

    rig.service.tick()

    assert rig.heartbeat.beats == 1
    assert rig.lease.renewals == [FIXED_NOW]


def test_no_heartbeat_is_written_when_the_tick_ended_by_a_system_wide_error() -> None:
    rig = Rig()
    rig.approvals.failure = CredentialsRevoked("revoked", next_action="Run make auth")

    rig.service.tick()

    assert rig.heartbeat.beats == 0
    assert rig.lease.renewals == []


# --- stopping on SIGTERM ---


def test_sigterm_finishes_the_email_in_hand_then_exits() -> None:
    rig = Rig()
    rig.inbox("first", "second")
    stop = threading.Event()
    rig.processor.on_process.append(stop.set)

    rig.service.run(stop)

    assert rig.processor.seen == ["first"]


def test_a_clean_stop_leaves_the_lock_in_place() -> None:
    rig = Rig()
    rig.inbox("first")
    stop = threading.Event()
    rig.processor.on_process.append(stop.set)

    rig.service.run(stop)

    assert rig.lease.holding is not None
    assert rig.lease.holding.copy_id == THIS_COPY


# --- the lock, at every tick ---


def test_a_lock_lost_mid_run_stops_before_acting() -> None:
    rig = Rig()
    rig.inbox("a")
    rig.lease.holding = LeaseHolding(OTHER_COPY, FIXED_NOW)

    with pytest.raises(LockLost) as stopped:
        rig.service.tick()

    assert rig.processor.seen == []
    assert rig.approvals.runs == 0
    assert stopped.value.next_action


def test_a_lost_lock_stops_the_run_instead_of_backing_off() -> None:
    rig = Rig()
    rig.lease.holding = None

    with pytest.raises(LockLost):
        rig.service.run(threading.Event())

    assert rig.heartbeat.beats == 0


# --- the lock, at startup ---


def test_a_fresh_lock_from_another_copy_stops_startup_with_three_next_steps() -> None:
    lease = FakeLease(LeaseHolding(OTHER_COPY, FIXED_NOW - timedelta(seconds=10)))

    with pytest.raises(ConfigError) as refused:
        a_lock(lease).acquire()

    message = f"{refused.value}\n{refused.value.next_action}"
    assert "Stop the other copy" in message
    assert f"older than {int(STALE_AFTER.total_seconds())} seconds" in message
    assert "FORCE_LOCK=true" in message
    assert lease.holding == LeaseHolding(OTHER_COPY, FIXED_NOW - timedelta(seconds=10))


def test_a_stale_lock_is_taken() -> None:
    lease = FakeLease(LeaseHolding(OTHER_COPY, FIXED_NOW - STALE_AFTER))

    a_lock(lease).acquire()

    assert lease.holding == LeaseHolding(THIS_COPY, FIXED_NOW)


def test_force_lock_takes_a_fresh_lock() -> None:
    lease = FakeLease(LeaseHolding(OTHER_COPY, FIXED_NOW))

    a_lock(lease, force=True).acquire()

    assert lease.holding == LeaseHolding(THIS_COPY, FIXED_NOW)


def test_with_no_lock_at_all_the_lock_is_made() -> None:
    lease = FakeLease()

    a_lock(lease).acquire()

    assert lease.holding == LeaseHolding(THIS_COPY, FIXED_NOW)


def test_the_stale_limit_is_three_polls() -> None:
    lease = FakeLease()

    a_lock(lease).acquire()

    assert lease.requests[0].stale_after == STALE_AFTER
