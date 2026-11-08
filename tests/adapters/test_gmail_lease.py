"""The copy lock as a Gmail draft: found with drafts.list, renewed in place, never sent."""

from datetime import UTC, datetime, timedelta
from email import message_from_bytes, policy

import pytest
from fakes.fake_drafts_service import FakeDraftsService
from fakes.google_stub import GoogleStub, http_error

from aeca.adapters.gmail_lease import LOCK_SUBJECT, GmailLease
from aeca.domain.errors import LockLost, SystemWideError
from aeca.domain.lease import LeaseHolding, LeaseOutcome, LeaseRequest

NOW = datetime(2026, 11, 7, 12, 0, tzinfo=UTC)
STALE_AFTER = timedelta(seconds=90)
MINE = "mine"
THEIRS = "theirs"


def request(*, force: bool = False, now: datetime = NOW) -> LeaseRequest:
    return LeaseRequest(copy_id=MINE, now=now, stale_after=STALE_AFTER, force=force)


def lock_drafts(service: FakeDraftsService) -> list[str]:
    return [i for i, raw in service.stored.items() if LOCK_SUBJECT in raw.decode()]


def test_with_no_lock_the_draft_is_created_with_no_recipient_and_a_kind_header() -> None:
    service = FakeDraftsService()

    result = GmailLease(service).acquire(request())

    [draft_id] = lock_drafts(service)
    mime = message_from_bytes(service.raw_of(draft_id), policy=policy.default)
    assert result.outcome is LeaseOutcome.ACQUIRED
    assert mime["Subject"] == LOCK_SUBJECT
    assert mime["X-AECA-Kind"] == "lock"
    assert mime["To"] is None and mime["Cc"] is None and mime["Bcc"] is None
    assert MINE in mime.get_body().get_content()  # type: ignore[union-attr]
    assert "send" not in service.calls


def test_the_lock_is_found_among_other_drafts_on_a_later_page() -> None:
    service = FakeDraftsService(page_size=2)
    for number in range(5):
        service.add(subject=f"my own draft {number}")
    lock_id = service.add(
        subject=LOCK_SUBJECT, body=f"copy_id: {THEIRS}\nheartbeat: {NOW.isoformat()}\n"
    )

    result = GmailLease(service).acquire(request())

    assert result.outcome is LeaseOutcome.HELD_BY_ANOTHER
    assert result.holding == LeaseHolding(THEIRS, NOW)
    assert lock_drafts(service) == [lock_id]


def test_a_fresh_lock_of_another_copy_is_left_untouched() -> None:
    service = FakeDraftsService()
    lock_id = service.add(
        subject=LOCK_SUBJECT, body=f"copy_id: {THEIRS}\nheartbeat: {NOW.isoformat()}\n"
    )
    before = service.raw_of(lock_id)

    GmailLease(service).acquire(request(now=NOW + timedelta(seconds=5)))

    assert service.raw_of(lock_id) == before
    assert "update" not in service.calls and "create" not in service.calls


def test_a_stale_lock_is_updated_in_place_keeping_the_draft() -> None:
    service = FakeDraftsService()
    lock_id = service.add(
        subject=LOCK_SUBJECT, body=f"copy_id: {THEIRS}\nheartbeat: {NOW.isoformat()}\n"
    )

    result = GmailLease(service).acquire(request(now=NOW + STALE_AFTER))

    assert result.outcome is LeaseOutcome.TOOK_OVER_STALE
    assert lock_drafts(service) == [lock_id]
    assert MINE in service.raw_of(lock_id).decode()
    assert "create" not in service.calls


def test_force_takes_a_fresh_lock() -> None:
    service = FakeDraftsService()
    lock_id = service.add(
        subject=LOCK_SUBJECT, body=f"copy_id: {THEIRS}\nheartbeat: {NOW.isoformat()}\n"
    )

    result = GmailLease(service).acquire(request(force=True))

    assert result.outcome is LeaseOutcome.FORCED
    assert GmailLease(service).still_held(MINE)
    assert lock_drafts(service) == [lock_id]


def test_a_lock_with_a_body_nobody_can_read_is_taken() -> None:
    service = FakeDraftsService()
    service.add(subject=LOCK_SUBJECT, body="somebody edited this by hand")

    result = GmailLease(service).acquire(request())

    assert result.outcome is LeaseOutcome.ACQUIRED


def test_still_held_is_true_only_for_the_copy_named_in_the_draft() -> None:
    service = FakeDraftsService()
    lease = GmailLease(service)
    lease.acquire(request())

    assert lease.still_held(MINE)
    assert not lease.still_held(THEIRS)


def test_still_held_is_false_when_another_copy_has_taken_the_lock() -> None:
    service = FakeDraftsService()
    lease = GmailLease(service)
    lease.acquire(request())
    GmailLease(service).acquire(
        LeaseRequest(copy_id=THEIRS, now=NOW, stale_after=STALE_AFTER, force=True)
    )

    assert not lease.still_held(MINE)


def test_still_held_is_false_when_the_draft_was_deleted() -> None:
    service = FakeDraftsService()
    lease = GmailLease(service)
    lease.acquire(request())
    service.stored.clear()

    assert not lease.still_held(MINE)


def test_renew_writes_the_new_heartbeat_to_the_same_draft() -> None:
    service = FakeDraftsService()
    lease = GmailLease(service)
    lease.acquire(request())
    [lock_id] = lock_drafts(service)
    later = NOW + timedelta(seconds=30)

    lease.renew(MINE, later)

    assert lock_drafts(service) == [lock_id]
    assert later.isoformat() in service.raw_of(lock_id).decode()


def test_renew_refuses_when_another_copy_took_the_lock() -> None:
    service = FakeDraftsService()
    lease = GmailLease(service)
    lease.acquire(request())
    GmailLease(service).acquire(
        LeaseRequest(copy_id=THEIRS, now=NOW, stale_after=STALE_AFTER, force=True)
    )
    taken = service.raw_of(lock_drafts(service)[0])

    with pytest.raises(LockLost):
        lease.renew(MINE, NOW + timedelta(seconds=30))

    assert service.raw_of(lock_drafts(service)[0]) == taken


def test_the_lock_draft_is_never_sent_and_never_deleted() -> None:
    service = FakeDraftsService()
    lease = GmailLease(service)

    lease.acquire(request())
    lease.renew(MINE, NOW + timedelta(seconds=30))
    lease.still_held(MINE)

    assert {"send", "delete"}.isdisjoint(service.calls)


def test_a_google_failure_reaches_the_caller_as_an_aeca_error() -> None:
    stub = GoogleStub()
    stub.reply("users.drafts.list", http_error(503))

    with pytest.raises(SystemWideError):
        GmailLease(stub).acquire(request())


def _body_of(copy_id: str, heartbeat: datetime = NOW) -> str:
    return f"copy_id: {copy_id}\nheartbeat: {heartbeat.isoformat()}\n"


def test_with_two_lock_drafts_the_lowest_id_is_the_lock() -> None:
    service = FakeDraftsService()
    service.add(subject=LOCK_SUBJECT, body=_body_of(THEIRS), draft_id="draft-1")
    service.add(subject=LOCK_SUBJECT, body=_body_of(MINE), draft_id="draft-2")
    lease = GmailLease(service)

    result = lease.acquire(request(now=NOW + timedelta(seconds=5)))

    assert result.outcome is LeaseOutcome.HELD_BY_ANOTHER
    assert not lease.still_held(MINE)
    assert {"update", "delete", "send"}.isdisjoint(service.calls)


def test_a_copy_that_lost_the_creation_race_does_not_hold_the_lock() -> None:
    service = FakeDraftsService()

    def another_copy_created_a_lower_draft(drafts: FakeDraftsService) -> None:
        drafts.add(subject=LOCK_SUBJECT, body=_body_of(THEIRS), draft_id="draft-0")

    service.after_create = another_copy_created_a_lower_draft
    lease = GmailLease(service)

    result = lease.acquire(request())

    assert result.outcome is LeaseOutcome.HELD_BY_ANOTHER
    assert result.holding == LeaseHolding(THEIRS, NOW)
    assert not lease.still_held(MINE)
    assert "delete" not in service.calls


def test_a_copy_that_won_the_creation_race_holds_the_lock() -> None:
    service = FakeDraftsService()

    def another_copy_created_a_higher_draft(drafts: FakeDraftsService) -> None:
        drafts.add(subject=LOCK_SUBJECT, body=_body_of(THEIRS), draft_id="draft-9")

    service.after_create = another_copy_created_a_higher_draft
    lease = GmailLease(service)

    result = lease.acquire(request())

    assert result.outcome is LeaseOutcome.ACQUIRED
    assert lease.still_held(MINE)
