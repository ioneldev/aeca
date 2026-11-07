"""Running unattended: the heartbeat, the copy lock and the stop on SIGTERM."""

import itertools
import threading
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from fakes.fake_lease import FakeLease
from fakes.fake_mailbox import FakeMailbox
from fakes.fixed_clock import FIXED_NOW
from fakes.recording_console import RecordingConsole
from support.audit_lines import line_for
from support.quiet_service import nothing_to_start, quiet_watch

from aeca.adapters.copy_id_file import load_or_create_copy_id
from aeca.application.copy_lock import CopyLock, CopyLockDeps
from aeca.application.service import Service, ServiceDeps
from aeca.domain.audit import AuditLine
from aeca.domain.errors import (
    ConfigError,
    CredentialsRevoked,
    EmailError,
    LockLost,
    SystemWideError,
)
from aeca.domain.lease import LeaseHolding

POLL_SECONDS = 30
THIS_COPY = "copy-this"
OTHER_COPY = "copy-other"
STALE_AFTER = timedelta(seconds=3 * POLL_SECONDS)


class MovableClock:
    def __init__(self) -> None:
        self.current = FIXED_NOW

    def now(self) -> datetime:
        return self.current

    def pass_seconds(self, seconds: float) -> None:
        self.current += timedelta(seconds=seconds)


class CountingHeartbeat:
    def __init__(self, clock: MovableClock) -> None:
        self.beats = 0
        self.times: list[datetime] = []
        self._clock = clock

    def beat(self) -> None:
        self.beats += 1
        self.times.append(self._clock.now())


class FailingLease(FakeLease):
    """A lease whose Gmail calls fail as scripted, once armed."""

    def __init__(self, holding: LeaseHolding | None) -> None:
        super().__init__(holding)
        self.still_held_failure: Exception | None = None
        self.renew_failure: Exception | None = None

    def still_held(self, copy_id: str) -> bool:
        if self.still_held_failure is not None:
            raise self.still_held_failure
        return super().still_held(copy_id)

    def renew(self, copy_id: str, now: datetime) -> None:
        if self.renew_failure is not None:
            raise self.renew_failure
        super().renew(copy_id, now)


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


def a_lock(lease: FakeLease, *, force: bool = False, clock: MovableClock | None = None) -> CopyLock:
    return CopyLock(
        CopyLockDeps(
            lease=lease,
            clock=clock or MovableClock(),
            copy_id=THIS_COPY,
            poll_seconds=POLL_SECONDS,
            force=force,
        )
    )


class Rig:
    def __init__(self, lease: FakeLease | None = None) -> None:
        self.clock = MovableClock()
        self.lease = lease or FakeLease(LeaseHolding(THIS_COPY, FIXED_NOW))
        self.mailbox = FakeMailbox()
        self.processor = Processor()
        self.approvals = Approvals()
        self.heartbeat = CountingHeartbeat(self.clock)
        self.console = RecordingConsole()
        self.service = Service(
            ServiceDeps(
                processor=self.processor,
                mailbox=self.mailbox,
                console=self.console,
                poll_seconds=0.01,
                approvals=self.approvals,
                watch=quiet_watch(),
                start_up=nothing_to_start,
                copy_lock=a_lock(self.lease, clock=self.clock),
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
    assert rig.console.kinds()[-1] == "service_stopped"
    assert rig.heartbeat.beats == 1


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


def test_the_lock_lost_after_the_first_of_three_emails_stops_before_the_second() -> None:
    rig = Rig()
    rig.inbox("a", "b", "c")
    rig.processor.on_process.append(
        lambda: setattr(rig.lease, "holding", LeaseHolding(OTHER_COPY, FIXED_NOW))
    )

    with pytest.raises(LockLost):
        rig.service.tick()

    assert rig.processor.seen == ["a"]
    assert rig.heartbeat.beats == 0


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


# --- a failing Gmail call in the lock backs off, it never ends the service ---


@pytest.mark.parametrize(
    "failure", [SystemWideError("503", next_action="Wait"), EmailError("403", next_action="See")]
)
@pytest.mark.parametrize("failing_call", ["still_held_failure", "renew_failure"])
def test_a_gmail_error_in_the_lock_backs_off(failure: Exception, failing_call: str) -> None:
    lease = FailingLease(LeaseHolding(THIS_COPY, FIXED_NOW))
    setattr(lease, failing_call, failure)
    rig = Rig(lease)
    rig.inbox("a")

    result = rig.service.tick()

    assert result.backoff_s > 0
    assert rig.heartbeat.beats == 0
    assert "backoff" in rig.console.kinds()


def test_a_lost_lock_is_not_mistaken_for_an_outage() -> None:
    lease = FailingLease(LeaseHolding(THIS_COPY, FIXED_NOW))
    lease.renew_failure = LockLost.for_a_lost_lock()
    rig = Rig(lease)

    with pytest.raises(LockLost):
        rig.service.tick()


# --- a long tick keeps the lock and the heartbeat alive ---


def test_a_tick_as_long_as_three_polls_keeps_the_heartbeat_fresh() -> None:
    rig = Rig()
    rig.inbox("a", "b", "c", "d", "e")
    seconds_per_email = 3 * POLL_SECONDS / 5
    rig.processor.on_process.append(lambda: rig.clock.pass_seconds(seconds_per_email))

    rig.service.tick()

    beats = [FIXED_NOW, *rig.heartbeat.times]
    gaps = [later - earlier for earlier, later in itertools.pairwise(beats)]
    assert len(rig.heartbeat.times) >= 3
    assert max(gaps) <= timedelta(seconds=POLL_SECONDS) + timedelta(seconds=seconds_per_email)
    assert rig.lease.renewals[-1] == rig.clock.now()


def test_a_short_tick_renews_once_after_the_poll() -> None:
    rig = Rig()
    rig.inbox("a", "b")

    rig.service.tick()

    assert rig.heartbeat.beats == 1
    assert len(rig.lease.renewals) == 1


def test_an_outage_does_not_renew_the_lock() -> None:
    rig = Rig()
    rig.approvals.failure = CredentialsRevoked("revoked", next_action="Run make auth")
    rig.clock.pass_seconds(10 * POLL_SECONDS)

    rig.service.tick()

    assert rig.lease.renewals == []
    assert rig.heartbeat.beats == 0


# --- a restart of the same installation reclaims its own lock ---


def test_a_restart_with_the_same_copy_id_acquires_at_once() -> None:
    lease = FakeLease(LeaseHolding(THIS_COPY, FIXED_NOW - timedelta(seconds=10)))

    a_lock(lease).acquire()

    assert lease.holding == LeaseHolding(THIS_COPY, FIXED_NOW)


def test_a_restart_with_the_same_data_dir_keeps_the_copy_id(tmp_path: Path) -> None:
    before_restart = load_or_create_copy_id(tmp_path)
    lease = FakeLease(LeaseHolding(before_restart, FIXED_NOW - timedelta(seconds=10)))

    CopyLock(
        CopyLockDeps(
            lease=lease,
            clock=MovableClock(),
            copy_id=load_or_create_copy_id(tmp_path),
            poll_seconds=POLL_SECONDS,
            force=False,
        )
    ).acquire()

    assert lease.holding == LeaseHolding(before_restart, FIXED_NOW)


def test_a_different_data_dir_is_refused_while_the_lock_is_fresh(tmp_path: Path) -> None:
    lease = FakeLease(LeaseHolding(load_or_create_copy_id(tmp_path / "one"), FIXED_NOW))

    with pytest.raises(ConfigError):
        CopyLock(
            CopyLockDeps(
                lease=lease,
                clock=MovableClock(),
                copy_id=load_or_create_copy_id(tmp_path / "two"),
                poll_seconds=POLL_SECONDS,
                force=False,
            )
        ).acquire()


def test_a_heartbeat_from_the_future_never_prints_a_negative_age() -> None:
    lease = FakeLease(LeaseHolding(OTHER_COPY, FIXED_NOW + timedelta(seconds=20)))

    with pytest.raises(ConfigError) as refused:
        a_lock(lease).acquire()

    assert "-" not in str(refused.value).split("heartbeat is")[1].split(" s old")[0]
