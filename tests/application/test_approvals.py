"""Approvals by email: the request, which reply counts, how it is read, and executing exactly once.

The spec's blocking cases 2, 3, 4 and 5 (§5) are here, with its "Approval replies" cases. A kill
is a Killed raised by a fake right after a Google write: no recovery code runs, exactly as when
the process dies.
"""

from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from fakes.crash import Killed
from support.approval_rig import (
    COLLEAGUE,
    MANAGER,
    MEETING,
    SOURCE_ID,
    SOURCE_THREAD,
    TUESDAY_SLOT,
    ApprovalRig,
    ApprovalRigOptions,
)
from support.decisions import decision_of
from support.emails import make_raw
from support.inbox_rig import ACCOUNT, BUCHAREST

from aeca.application.ports import OpenApproval
from aeca.domain.actions import Action, ActionStatus, Verb
from aeca.domain.approval import ApprovalState
from aeca.domain.audit import AuditLine
from aeca.domain.calendar_event import CalendarEvent
from aeca.domain.email import RawEmail
from aeca.domain.errors import EmailError, SystemWideError
from aeca.domain.facts import extract_facts
from aeca.domain.ids import approval_id, event_id
from aeca.domain.labels import AWAITING, PROCESSED, SENT, Label, ReplyKind
from aeca.domain.policy import alternatives_reply
from aeca.domain.time_range import Interval

FIRST_EVENT_ID = event_id(SOURCE_ID, 0)
URGENT_DRAFT = "On it. Approve the budget today, I will call you."


@pytest.fixture
def rig(tmp_path: Path) -> ApprovalRig:
    return ApprovalRig(tmp_path)


def rig_with(state_dir: Path, **options: object) -> ApprovalRig:
    return ApprovalRig(state_dir, ApprovalRigOptions(**options))  # type: ignore[arg-type]


def opened(rig: ApprovalRig) -> ApprovalRig:
    rig.receive_meeting_request()
    return rig


def lines_about(rig: ApprovalRig, message_id: str) -> list[AuditLine]:
    return [line for line in rig.audit.lines if line.message_id == message_id]


def only_line_about(rig: ApprovalRig, message_id: str) -> AuditLine:
    [line] = lines_about(rig, message_id)
    return line


def event_at(event_id_: str, *, cancelled: bool = False) -> CalendarEvent:
    return CalendarEvent(
        id=event_id_,
        interval=TUESDAY_SLOT,
        all_day=False,
        cancelled=cancelled,
        declined_by_me=False,
        ical_uid=None,
    )


def assert_executed_once(rig: ApprovalRig) -> None:
    assert rig.approval().state is ApprovalState.EXECUTED
    assert [event.id for event in rig.calendar.inserted] == [FIRST_EVENT_ID]
    assert rig.kinds_sent().count("confirmation") == 1


def assert_nothing_executed(rig: ApprovalRig) -> None:
    assert rig.approval().state is ApprovalState.AWAITING
    assert rig.calendar.inserted == []
    assert "confirmation" not in rig.kinds_sent()


# --- the request (spec §4.5) ---


@pytest.mark.parametrize(("manager_address", "expected"), [(MANAGER, MANAGER), (None, ACCOUNT)])
def test_the_request_goes_to_manager_address_when_set_else_to_self(
    tmp_path: Path, manager_address: str | None, expected: str
) -> None:
    rig = opened(rig_with(tmp_path, manager_address=manager_address))

    [request] = rig.requests_sent()
    assert request.to == expected
    assert (request.kind, request.aeca_id) == ("approval-request", approval_id(SOURCE_ID, 0))
    assert (request.thread_id, request.in_reply_to) == (None, None)
    assert request.subject.startswith("[AECA] Approve: ")


def test_opening_saves_the_approval_with_its_holds_and_records_the_request_thread(
    rig: ApprovalRig,
) -> None:
    opened(rig)

    approval = rig.approval()
    [request_message] = [
        summary for summary in rig.mailbox.thread_summaries(approval.request_thread_id or "")
    ]
    assert approval.state is ApprovalState.AWAITING
    assert approval.holds == (TUESDAY_SLOT,)
    assert [hold.approval_id for hold in rig.store.holds_active(rig.inbox.clock.now())] == [
        approval.id
    ]
    assert request_message.aeca_id == approval.id
    assert PROCESSED in request_message.label_ids
    assert {AWAITING, PROCESSED, "MEETING_REQUEST"} <= rig.mailbox.labels_of(SOURCE_ID)


def test_the_request_shows_the_event_and_the_confirmation_in_the_calendars_zone(
    rig: ApprovalRig,
) -> None:
    opened(rig)

    [request] = rig.requests_sent()
    assert "Tuesday 10 November 2026, 15:00\u201315:30 (Europe/Bucharest)" in request.body
    assert "| Thank you for your request. The meeting is confirmed for:" in request.body
    assert "To answer, reply with APPROVE or REJECT as the first word" in request.body


def test_an_urgent_internal_request_carries_the_notice_and_the_prefixed_draft(
    rig: ApprovalRig,
) -> None:
    rig.receive_meeting_request(
        decision_of(Label.TASK, urgent=True, urgent_quote="today", urgent_draft=URGENT_DRAFT)
    )

    [request] = rig.requests_sent()
    assert "looks urgent" in request.body
    assert "| On it. Approve the budget today, I will call you." in request.body
    assert "urgent-notice" not in rig.kinds_sent()


def test_opening_again_returns_the_same_approval_and_sends_nothing_more(rig: ApprovalRig) -> None:
    opened(rig)
    first = rig.approval()

    rig.inbox.processor.process(SOURCE_ID)
    rig.tick()

    assert rig.approval() == first
    assert len(rig.requests_sent()) == 1


def kill_while_requesting(rig: ApprovalRig, kill: str) -> None:
    if kill == "before-send":
        rig.mailbox.send_failures.append(Killed())
    else:
        rig.mailbox.kill_after_entry = kill


@pytest.mark.parametrize("kill", ["before-send", "send:approval-request", f"modify:{PROCESSED}"])
def test_a_request_is_sent_once_after_a_crash_before_the_state_write(
    rig: ApprovalRig, kill: str
) -> None:
    kill_while_requesting(rig, kill)

    with pytest.raises(Killed):
        rig.receive_meeting_request()
    rig.restart()
    rig.tick()

    assert len(rig.requests_sent()) == 1
    assert rig.journal.count("draft:approval-request") == 1
    assert rig.mailbox.drafts == {}
    assert rig.approval().request_thread_id is not None
    assert PROCESSED in rig.mailbox.labels_of(SOURCE_ID)


def test_a_draft_made_but_not_yet_recorded_is_never_sent(rig: ApprovalRig) -> None:
    rig.mailbox.kill_after_entry = "draft:approval-request"

    with pytest.raises(Killed):
        rig.receive_meeting_request()
    rig.restart()
    rig.tick()

    assert len(rig.requests_sent()) == 1
    assert rig.approval().request_thread_id is not None


@pytest.mark.parametrize(
    "kill_after_entry", ["send:approval-request", f"modify:{PROCESSED}", f"modify:{AWAITING}"]
)
def test_a_request_is_sent_once_when_the_volume_is_wiped_after_a_crash(
    rig: ApprovalRig, kill_after_entry: str
) -> None:
    rig.mailbox.kill_after_entry = kill_after_entry

    with pytest.raises(Killed):
        rig.receive_meeting_request()
    rig.restart_with_the_volume_wiped()
    rig.tick()

    assert len(rig.requests_sent()) == 1
    assert rig.approval().request_thread_id is not None


def test_an_approval_left_without_a_request_gets_one_on_the_next_tick(rig: ApprovalRig) -> None:
    opened(rig)
    approval = rig.approval()
    rig.store.approval_save(replace(approval, id="a-legacy", request_thread_id=None, actions=()))
    sent_before = len(rig.requests_sent())

    rig.tick()
    rig.tick()

    legacy = rig.store.approval_get("a-legacy")
    assert legacy is not None and legacy.request_thread_id is not None
    assert len(rig.requests_sent()) == sent_before + 1


# --- who can approve (blocking case 5) ---


@pytest.mark.parametrize("manager_address", [MANAGER, None])
def test_a_sent_reply_counts(tmp_path: Path, manager_address: str | None) -> None:
    rig = opened(rig_with(tmp_path, manager_address=manager_address))
    rig.answer_from_account("APPROVE")

    rig.service.process_all()

    assert_executed_once(rig)


@pytest.mark.parametrize("manager_address", [MANAGER, None, ACCOUNT])
def test_an_unsent_reply_spoofed_as_the_account_is_ignored_and_audited(
    tmp_path: Path, manager_address: str | None
) -> None:
    rig = opened(rig_with(tmp_path, manager_address=manager_address))
    spoofed = rig.answer("APPROVE", sender=ACCOUNT, dmarc="pass")

    rig.service.process_all()

    assert_nothing_executed(rig)
    line = only_line_about(rig, spoofed)
    assert line.outcome == "ignored"
    assert line.rules == ("answer_ignored:from_the_account_without_sent",)
    assert (line.approval_id, line.approval_state) == (rig.approval().id, "awaiting")
    assert PROCESSED in rig.mailbox.labels_of(spoofed)


def test_a_manager_reply_with_dmarc_pass_counts(rig: ApprovalRig) -> None:
    opened(rig)
    rig.answer("Approve.", sender="Boss@Example.com")

    rig.service.process_all()

    assert_executed_once(rig)


@pytest.mark.parametrize("dmarc", ["fail", None])
def test_a_manager_reply_with_dmarc_fail_is_ignored(rig: ApprovalRig, dmarc: str | None) -> None:
    opened(rig)
    forged = rig.answer("APPROVE", dmarc=dmarc)

    rig.service.process_all()

    assert_nothing_executed(rig)
    line = only_line_about(rig, forged)
    assert (line.outcome, line.rules) == ("ignored", ("answer_ignored:manager_dmarc_not_pass",))


def test_a_manager_reply_from_an_unset_manager_address_is_ignored(tmp_path: Path) -> None:
    rig = opened(rig_with(tmp_path, manager_address=None))
    stranger = rig.answer("APPROVE")

    rig.service.process_all()

    assert_nothing_executed(rig)
    assert only_line_about(rig, stranger).rules == ("answer_ignored:not_sent_by_the_account",)


def test_a_manager_reply_outside_the_request_thread_is_ignored(rig: ApprovalRig) -> None:
    opened(rig)
    rig.inbox.next_decision = decision_of(Label.TASK)
    rig.answer("APPROVE", thread_id="another-thread")
    rig.answer("APPROVE", thread_id=SOURCE_THREAD)

    rig.tick()
    rig.tick()

    assert_nothing_executed(rig)


# --- AECA's own words never approve (blocking case 4) ---


def test_aeca_messages_never_count_even_with_approve_in_subject_or_name_and_a_fresh_store(
    rig: ApprovalRig,
) -> None:
    opened(rig)
    approval = rig.approval()
    thread = rig.request_thread()
    decided_before = list(rig.inbox.decided)
    # AECA's own mail as Gmail holds it, SENT and with AECA's headers, but saying APPROVE in the
    # sender's name, the subject and the first word of the body.
    for number, kind in enumerate(("approval-request", "approval-help", "urgent-notice")):
        rig.mailbox.add(
            make_raw(
                sender=f'"APPROVE" <{ACCOUNT}>',
                to=MANAGER,
                subject="APPROVE",
                body="APPROVE\nREJECT",
                headers={"X-AECA-Kind": kind, "X-AECA-Id": approval.id},
                label_ids=("SENT", "INBOX"),
                message_id=f"aeca-{number}",
                thread_id=thread,
                internal_date=datetime(2026, 11, 7, 9, 30 + number, tzinfo=BUCHAREST),
            )
        )
    rig.restart_with_the_volume_wiped()
    rig.store.approval_save(approval)

    rig.tick()
    rig.tick()

    assert_nothing_executed(rig)
    assert rig.inbox.decided == decided_before
    assert all(line.approval_state not in {"approved", "executed"} for line in rig.audit.lines)


def test_an_aeca_header_on_a_reply_from_the_manager_does_not_count_either(
    rig: ApprovalRig,
) -> None:
    opened(rig)
    rig.answer("APPROVE", headers={"X-AECA-Kind": "approval-request"})

    rig.service.process_all()

    assert_nothing_executed(rig)


# --- reading the answer ---


def test_an_html_only_reply_is_read(rig: ApprovalRig) -> None:
    opened(rig)
    rig.answer(None, html="<div dir='ltr'>APPROVE</div><blockquote>REJECT</blockquote>")

    rig.service.process_all()

    assert_executed_once(rig)


def test_a_reply_written_below_the_quoted_request_is_read(rig: ApprovalRig) -> None:
    opened(rig)
    rig.answer("> To answer, reply with APPROVE or REJECT\n>\n\u00a0Reject")

    rig.service.process_all()

    assert rig.approval().state is ApprovalState.REJECTED


def test_the_first_decisive_answer_wins_and_later_ones_are_logged(rig: ApprovalRig) -> None:
    opened(rig)
    first = rig.answer("APPROVE")
    late = rig.answer("REJECT")

    rig.service.process_all()

    assert_executed_once(rig)
    assert only_line_about(rig, first).approval_state == "executed"
    late_line = only_line_about(rig, late)
    assert (late_line.outcome, late_line.rules) == ("ignored", ("answer_after_the_decision",))
    assert PROCESSED in rig.mailbox.labels_of(late)


def test_a_reject_first_wins_over_a_later_approve(rig: ApprovalRig) -> None:
    opened(rig)
    rig.answer("Rejected")
    late = rig.answer("APPROVED")

    rig.service.process_all()
    rig.service.process_all()

    assert rig.approval().state is ApprovalState.REJECTED
    assert rig.calendar.inserted == []
    assert only_line_about(rig, late).rules == ("answer_after_the_decision",)


def test_an_answer_is_read_once(rig: ApprovalRig) -> None:
    opened(rig)
    ignored = rig.answer("APPROVE", dmarc="fail")

    rig.service.process_all()
    rig.service.process_all()

    assert rig.mailbox.get_log.count(ignored) == 1
    assert len(lines_about(rig, ignored)) == 1


def test_a_draft_in_the_request_thread_is_neither_read_nor_marked_seen(rig: ApprovalRig) -> None:
    opened(rig)
    draft = rig.answer("APPROVE", sender=ACCOUNT, dmarc=None, label_ids=("DRAFT",))

    rig.service.process_all()

    assert_nothing_executed(rig)
    assert draft not in rig.mailbox.get_log
    assert not rig.store.answer_seen(draft)


def test_the_model_never_reads_an_approval_reply(rig: ApprovalRig) -> None:
    opened(rig)
    decided_before = list(rig.inbox.decided)
    rig.answer("Yes, go ahead")
    rig.answer("APPROVE")

    rig.tick()
    rig.tick()

    assert rig.inbox.decided == decided_before
    assert_executed_once(rig)


# --- executing (blocking case 2) ---


def test_approve_creates_the_event_then_sends_the_confirmation_in_order(rig: ApprovalRig) -> None:
    opened(rig)
    answer = rig.answer("APPROVE")
    rig.journal.clear()

    rig.service.process_all()

    assert rig.journal.index("insert_event") < rig.journal.index("send:confirmation")
    [event] = rig.calendar.inserted
    assert (event.id, event.title, event.interval) == (FIRST_EVENT_ID, "Sync", TUESDAY_SLOT)
    assert rig.approval().id in event.description and COLLEAGUE in event.description
    [confirmation] = [m for m in rig.mailbox.sent if m.kind == "confirmation"]
    assert (confirmation.to, confirmation.thread_id) == (COLLEAGUE, SOURCE_THREAD)
    line = only_line_about(rig, answer)
    assert (line.outcome, line.approval_state, line.decided_by) == ("done", "executed", "manager")
    assert line.actions == (
        ("create_event", "", "done"),
        ("send_reply", "confirmation", "done"),
    )


def test_a_live_event_under_the_id_is_not_created_again(rig: ApprovalRig) -> None:
    opened(rig)
    rig.calendar.add(event_at(FIRST_EVENT_ID))
    rig.answer("APPROVE")

    rig.service.process_all()

    assert rig.calendar.inserted == []
    assert rig.kinds_sent().count("confirmation") == 1
    assert rig.approval().state is ApprovalState.EXECUTED


def test_a_cancelled_event_under_the_id_moves_to_the_next_attempt(rig: ApprovalRig) -> None:
    opened(rig)
    rig.calendar.add(event_at(FIRST_EVENT_ID, cancelled=True))
    rig.answer("APPROVE")

    rig.service.process_all()

    assert [event.id for event in rig.calendar.inserted] == [event_id(SOURCE_ID, 1)]
    live = rig.calendar.get_event(event_id(SOURCE_ID, 1))
    assert live is not None and not live.cancelled
    assert rig.kinds_sent().count("confirmation") == 1


APPROVE_FLOW_KILLS = [
    "insert",
    "draft:confirmation",
    "send:confirmation",
    f"modify:{PROCESSED}",
    f"modify:-{AWAITING}",
]


def kill_during_approval(rig: ApprovalRig, kill: str) -> None:
    if kill == "insert":
        rig.calendar.kill_after_insert = True
    else:
        rig.mailbox.kill_after_entry = kill
    with pytest.raises(Killed):
        rig.service.process_all()


@pytest.mark.parametrize("kill", APPROVE_FLOW_KILLS)
def test_a_calendar_that_accepts_duplicate_ids_still_gets_one_event(
    rig: ApprovalRig, kill: str
) -> None:
    opened(rig)
    rig.calendar.accepts_duplicate_ids = True
    rig.answer("APPROVE")
    kill_during_approval(rig, kill)

    rig.restart()
    rig.tick()
    rig.tick()

    assert len(rig.calendar.events_with_id(FIRST_EVENT_ID)) == 1
    assert len(rig.calendar.inserted) == 1
    assert rig.kinds_sent().count("confirmation") == 1
    assert rig.approval().state is ApprovalState.EXECUTED


@pytest.mark.parametrize("kill", APPROVE_FLOW_KILLS)
def test_a_wiped_volume_after_a_kill_while_executing_creates_and_sends_nothing_more(
    rig: ApprovalRig, kill: str
) -> None:
    opened(rig)
    rig.answer("APPROVE")
    kill_during_approval(rig, kill)
    inserted, sent = len(rig.calendar.inserted), rig.kinds_sent().count("confirmation")

    rig.restart_with_the_volume_wiped()
    rig.tick()

    assert len(rig.calendar.inserted) == inserted <= 1
    assert rig.kinds_sent().count("confirmation") == sent <= 1


@pytest.mark.parametrize("misbehaviour", ["inserts_vanish", "inserts_come_back_cancelled"])
def test_no_confirmation_without_a_live_event(rig: ApprovalRig, misbehaviour: str) -> None:
    opened(rig)
    setattr(rig.calendar, misbehaviour, True)
    rig.answer("APPROVE")

    for _ in range(4):
        rig.service.process_all()

    assert "confirmation" not in rig.kinds_sent()
    assert rig.approval().state is ApprovalState.FAILED
    [create_event, _confirmation] = rig.approval().actions
    record = rig.store.action_get(create_event.key)
    assert record is not None and record.status is ActionStatus.FAILED


def test_a_failed_approved_action_is_retried_with_the_same_key_twice_then_the_approval_fails(
    rig: ApprovalRig,
) -> None:
    opened(rig)
    rig.answer("APPROVE")
    rig.mailbox.send_failures = [EmailError("refused", next_action="x") for _ in range(3)]

    rig.service.process_all()
    after_first = rig.approval().state
    rig.service.process_all()
    after_second = rig.approval().state
    rig.service.process_all()

    assert (after_first, after_second) == (ApprovalState.APPROVED, ApprovalState.APPROVED)
    assert rig.approval().state is ApprovalState.FAILED
    assert rig.journal.count("draft:confirmation") == 1
    assert len(rig.calendar.inserted) == 1
    assert [line.outcome for line in rig.audit.lines[-3:]] == ["failed", "failed", "failed"]
    assert rig.audit.lines[-1].approval_state == "failed"
    assert AWAITING not in rig.mailbox.labels_of(SOURCE_ID)
    assert rig.store.holds_active(rig.inbox.clock.now()) == []


def test_a_retry_that_succeeds_executes_the_approval(rig: ApprovalRig) -> None:
    opened(rig)
    rig.answer("APPROVE")
    rig.mailbox.send_failures = [EmailError("refused", next_action="x")]

    rig.service.process_all()
    rig.service.process_all()

    assert_executed_once(rig)


def test_a_system_wide_error_while_executing_charges_no_try(rig: ApprovalRig) -> None:
    opened(rig)
    rig.answer("APPROVE")
    rig.mailbox.send_failures = [SystemWideError("Gmail is down", next_action="wait")] * 5

    for _ in range(5):
        with pytest.raises(SystemWideError):
            rig.service.process_all()
    rig.service.process_all()

    assert_executed_once(rig)


# --- a restart through production's start-up (spec §4.5, §4.6; final review I1) ---


def status_of(rig: ApprovalRig, action: Action) -> ActionStatus:
    record = rig.store.action_get(action.key)
    assert record is not None, f"{action.key} was never recorded"
    return record.status


def leave_approved_with_nothing_run(rig: ApprovalRig, cause: str) -> None:
    """The manager approved, and execution stopped before the event: by a Calendar outage, which
    ends the tick, or by a kill right after the answer was marked read."""
    opened(rig)
    rig.answer("APPROVE")
    if cause == "outage":
        outage = SystemWideError("Calendar answered 503", next_action="wait")
        rig.calendar.insert_failures.append(outage)
        with pytest.raises(SystemWideError):
            rig.service.process_all()
    else:
        rig.mailbox.kill_after_entry = f"modify:{PROCESSED}"
        with pytest.raises(Killed):
            rig.service.process_all()


@pytest.mark.parametrize("cause", ["outage", "kill"])
def test_a_restart_sends_no_confirmation_when_the_event_then_fails_for_this_email(
    rig: ApprovalRig, cause: str
) -> None:
    leave_approved_with_nothing_run(rig, cause)
    rig.calendar.insert_failures.append(
        EmailError("Google rejected the event (HTTP 400)", next_action="x")
    )

    rig.restart_through_start_up()
    rig.tick()

    [create_event, confirmation] = rig.approval().actions
    assert rig.calendar.inserted == []
    assert "confirmation" not in rig.kinds_sent()
    assert status_of(rig, create_event) is ActionStatus.FAILED
    assert status_of(rig, confirmation) is ActionStatus.PENDING
    assert rig.approval().state is ApprovalState.APPROVED


def test_start_up_resolves_an_event_left_executing_and_the_next_tick_confirms_once(
    rig: ApprovalRig,
) -> None:
    opened(rig)
    rig.answer("APPROVE")
    kill_during_approval(rig, "insert")
    [create_event, confirmation] = rig.approval().actions
    rig.store.action_update(create_event.key, ActionStatus.EXECUTING)

    rig.restart_through_start_up()
    after_start_up = (status_of(rig, create_event), status_of(rig, confirmation))
    rig.tick()

    assert after_start_up == (ActionStatus.DONE, ActionStatus.PENDING)
    assert_executed_once(rig)


def test_start_up_resolves_a_confirmation_left_executing_whose_draft_was_sent(
    rig: ApprovalRig,
) -> None:
    opened(rig)
    rig.answer("APPROVE")
    kill_during_approval(rig, "send:confirmation")
    [_create_event, confirmation] = rig.approval().actions
    left = status_of(rig, confirmation)

    rig.restart_through_start_up()

    assert left is ActionStatus.EXECUTING
    assert status_of(rig, confirmation) is ActionStatus.DONE
    sent_in_thread = [
        summary.aeca_kind
        for summary in rig.mailbox.thread_summaries(SOURCE_THREAD)
        if SENT in summary.label_ids
    ]
    assert sent_in_thread == ["confirmation"]
    assert rig.kinds_sent().count("confirmation") == 1


def test_an_emails_pending_acknowledgement_waits_for_the_journal_resume(
    rig: ApprovalRig,
) -> None:
    rig.inbox.next_decision = decision_of(Label.TASK, reply=ReplyKind.ACKNOWLEDGEMENT)
    rig.inbox.receive(sender=COLLEAGUE, message_id="m-task", thread_id="t-task")
    rig.mailbox.kill_after_entry = "modify:TASK"
    with pytest.raises(Killed):
        rig.tick()
    writes_before_the_restart = len(rig.journal)

    rig.restart_through_start_up()
    sent_at_start_up = rig.kinds_sent()
    rig.tick()

    assert sent_at_start_up == []
    assert rig.kinds_sent() == ["acknowledgement"]
    assert rig.journal[writes_before_the_restart:][:3] == [
        "modify:TASK",
        "draft:acknowledgement",
        "send:acknowledgement",
    ]


@pytest.mark.parametrize("kill", APPROVE_FLOW_KILLS)
def test_a_kill_while_executing_then_a_restart_through_start_up_executes_once(
    rig: ApprovalRig, kill: str
) -> None:
    opened(rig)
    rig.answer("APPROVE")
    kill_during_approval(rig, kill)

    rig.restart_through_start_up()
    rig.tick()
    rig.tick()

    assert_executed_once(rig)


# --- the re-check on APPROVE (blocking case 3) ---


def test_the_recheck_ignores_the_approvals_own_hold(rig: ApprovalRig) -> None:
    opened(rig)
    assert [hold.interval for hold in rig.store.holds_active(rig.inbox.clock.now())] == [
        TUESDAY_SLOT
    ]
    rig.answer("APPROVE")

    for _ in range(3):
        rig.tick()

    assert_executed_once(rig)
    assert len(rig.requests_sent()) == 1
    assert rig.store.approval_get(approval_id(SOURCE_ID, 1)) is None


def test_a_filled_slot_supersedes_and_opens_a_new_approval_with_alternatives(
    rig: ApprovalRig,
) -> None:
    opened(rig)
    rig.calendar.add(event_at("someone-elses-meeting"))
    answer = rig.answer("APPROVE")

    rig.service.process_all()

    old, new = rig.approval(0), rig.approval(1)
    assert (old.state, new.state) == (ApprovalState.SUPERSEDED, ApprovalState.AWAITING)
    [alternatives] = new.actions
    assert (alternatives.verb, alternatives.args["template"]) == (Verb.SEND_REPLY, "alternatives")
    assert new.holds and TUESDAY_SLOT not in new.holds
    held = {hold.approval_id for hold in rig.store.holds_active(rig.inbox.clock.now())}
    assert held == {new.id}
    assert rig.calendar.inserted == []
    assert [request.aeca_id for request in rig.requests_sent()] == [old.id, new.id]
    assert AWAITING in rig.mailbox.labels_of(SOURCE_ID)
    line = only_line_about(rig, answer)
    assert (line.approval_id, line.approval_state, line.rules) == (
        old.id,
        "superseded",
        ("slot_taken",),
    )


def test_approving_the_new_approval_sends_the_alternatives(rig: ApprovalRig) -> None:
    opened(rig)
    rig.calendar.add(event_at("someone-elses-meeting"))
    rig.answer("APPROVE")
    rig.service.process_all()

    rig.answer("APPROVE", thread_id=rig.request_thread(1))
    rig.service.process_all()

    assert rig.approval(1).state is ApprovalState.EXECUTED
    assert rig.kinds_sent().count("alternatives") == 1
    assert rig.calendar.inserted == []


def test_a_superseded_urgent_meeting_keeps_its_urgent_quote_in_the_new_approval(
    rig: ApprovalRig,
) -> None:
    rig.receive_meeting_request(
        decision_of(
            Label.MEETING_REQUEST,
            meeting=MEETING,
            urgent=True,
            urgent_quote="today",
            urgent_draft=URGENT_DRAFT,
        )
    )
    rig.calendar.add(event_at("someone-elses-meeting"))
    rig.answer("APPROVE")

    rig.service.process_all()

    templates = [action.args["template"] for action in rig.approval(1).actions]
    assert templates == ["alternatives"]
    assert rig.approval(1).actions[0].args["quote"] == "today"
    keys_before = {action.key for action in rig.approval(0).actions}
    assert keys_before.isdisjoint(action.key for action in rig.approval(1).actions)


def test_a_filled_slot_with_no_alternative_left_closes_the_approval(rig: ApprovalRig) -> None:
    opened(rig)
    busy_until_december = Interval(
        datetime(2026, 11, 7, 0, 0, tzinfo=BUCHAREST), datetime(2026, 12, 1, 0, 0, tzinfo=BUCHAREST)
    )
    rig.calendar.add(replace(event_at("a-busy-month"), interval=busy_until_december, all_day=True))
    rig.answer("APPROVE")

    rig.service.process_all()

    assert rig.approval().state is ApprovalState.SUPERSEDED
    assert rig.store.approval_get(approval_id(SOURCE_ID, 1)) is None
    assert AWAITING not in rig.mailbox.labels_of(SOURCE_ID)
    assert len(rig.requests_sent()) == 1


# --- reject and unclear answers ---


def test_reject_logs_releases_holds_and_keeps_the_label(rig: ApprovalRig) -> None:
    opened(rig)
    answer = rig.answer("REJECT")

    rig.service.process_all()

    assert rig.approval().state is ApprovalState.REJECTED
    line = only_line_about(rig, answer)
    assert (line.outcome, line.approval_state) == ("done", "rejected")
    assert rig.store.holds_active(rig.inbox.clock.now()) == []
    labels = rig.mailbox.labels_of(SOURCE_ID)
    assert "MEETING_REQUEST" in labels and AWAITING not in labels
    assert rig.calendar.inserted == []
    assert rig.kinds_sent() == ["approval-request"]


def test_an_unclear_answer_gets_one_help_reply(rig: ApprovalRig) -> None:
    opened(rig)
    first = rig.answer("Yes please")
    rig.answer("Sure, go ahead")

    rig.service.process_all()

    [help_message] = [message for message in rig.mailbox.sent if message.kind == "approval-help"]
    assert (help_message.to, help_message.thread_id) == (MANAGER, rig.request_thread())
    assert "reply with APPROVE or REJECT as the first word" in help_message.body
    assert rig.approval().state is ApprovalState.AWAITING
    line = only_line_about(rig, first)
    assert (line.outcome, line.rules) == ("awaiting_approval", ("answer_unclear",))

    rig.answer("APPROVE")
    rig.service.process_all()

    assert rig.kinds_sent().count("approval-help") == 1
    assert_executed_once(rig)


def test_the_help_reply_is_sent_once_after_a_crash(rig: ApprovalRig) -> None:
    opened(rig)
    rig.answer("Yes please")
    rig.mailbox.kill_after_entry = "send:approval-help"

    with pytest.raises(Killed):
        rig.service.process_all()
    rig.restart()
    rig.tick()

    assert rig.kinds_sent().count("approval-help") == 1


# --- closing an approval ---


def close_by(rig: ApprovalRig, how: str) -> None:
    if how == "failed":
        rig.calendar.inserts_vanish = True
        rig.answer("APPROVE")
        for _ in range(3):
            rig.service.process_all()
    else:
        rig.answer(how)
        rig.service.process_all()


@pytest.mark.parametrize(
    ("how", "state"),
    [
        ("APPROVE", ApprovalState.EXECUTED),
        ("REJECT", ApprovalState.REJECTED),
        ("failed", ApprovalState.FAILED),
    ],
)
def test_awaiting_is_removed_when_the_approval_closes(
    rig: ApprovalRig, how: str, state: ApprovalState
) -> None:
    opened(rig)
    assert AWAITING in rig.mailbox.labels_of(SOURCE_ID)

    close_by(rig, how)

    assert rig.approval().state is state
    assert AWAITING not in rig.mailbox.labels_of(SOURCE_ID)
    assert rig.store.holds_active(rig.inbox.clock.now()) == []
    assert rig.store.approvals_awaiting() == [] and rig.store.approvals_approved() == []


def test_the_gated_actions_of_a_rejected_approval_never_run(rig: ApprovalRig) -> None:
    opened(rig)
    rig.answer("REJECT")
    rig.service.process_all()

    for _ in range(3):
        rig.tick()

    for action in rig.approval().actions:
        record = rig.store.action_get(action.key)
        assert record is not None and record.status is ActionStatus.PENDING
    assert rig.store.actions_unfinished() == []


def test_an_answer_that_arrives_after_the_approval_closed_is_labelled_and_never_read(
    rig: ApprovalRig,
) -> None:
    opened(rig)
    rig.answer("REJECT")
    rig.tick()
    decided_before = list(rig.inbox.decided)
    late = rig.answer("APPROVE")

    rig.tick()
    rig.tick()

    assert rig.approval().state is ApprovalState.REJECTED
    assert PROCESSED in rig.mailbox.labels_of(late)
    assert rig.inbox.decided == decided_before
    assert rig.calendar.inserted == []
    line = only_line_about(rig, late)
    assert (line.outcome, line.rules, line.approval_state) == (
        "ignored",
        ("late_answer",),
        "rejected",
    )


def test_an_answer_waiting_for_an_open_approval_is_left_for_the_approval_loop(
    rig: ApprovalRig,
) -> None:
    opened(rig)
    waiting = rig.answer("APPROVE")

    rig.inbox.processor.process(waiting)

    assert PROCESSED not in rig.mailbox.labels_of(waiting)
    assert rig.approval().state is ApprovalState.AWAITING


def test_a_slot_that_has_started_by_the_approve_is_not_booked(rig: ApprovalRig) -> None:
    opened(rig)
    rig.restart_at(TUESDAY_SLOT.start)
    rig.answer("APPROVE")

    rig.service.process_all()

    assert rig.approval().state is ApprovalState.SUPERSEDED
    assert rig.calendar.inserted == []
    assert rig.approval(1).state is ApprovalState.AWAITING


def test_a_reject_whose_answer_cannot_be_labelled_is_still_audited_and_labelled_later(
    rig: ApprovalRig,
) -> None:
    opened(rig)
    answer = rig.answer("REJECT")
    refused = EmailError("label refused", next_action="none")
    rig.mailbox.fail_next_modify(answer, changing=PROCESSED, error=refused)

    rig.tick()
    rig.tick()

    assert rig.approval().state is ApprovalState.REJECTED
    assert only_line_about(rig, answer).approval_state == "rejected"
    assert PROCESSED in rig.mailbox.labels_of(answer)


# --- a DMARC pass judged for another From than the one AECA reads (blocking case 5) ---


def reply_with_headers(rig: ApprovalRig, header_lines: str, *, body: str = "APPROVE") -> str:
    """A reply in the request thread, its headers written by hand as a forger would."""
    rfc822 = (
        f"{header_lines}To: {ACCOUNT}\r\nSubject: Re: [AECA] Approve: meeting\r\n\r\n{body}\r\n"
    ).encode()
    rig.mailbox.add(
        RawEmail(
            message_id="hand-written",
            thread_id=rig.request_thread(),
            label_ids=frozenset({"INBOX"}),
            rfc822=rfc822,
            internal_date=datetime(2026, 11, 7, 11, 0, tzinfo=BUCHAREST),
        )
    )
    return "hand-written"


def dmarc_pass_for(domain: str) -> str:
    return (
        "Authentication-Results: mx.google.com; spf=pass; dkim=pass;"
        f" dmarc=pass header.from={domain}\r\n"
    )


def forged_by_header_differential(rig: ApprovalRig, from_lines: str) -> str:
    """Gmail judged DMARC for the attacker's own domain, and passed it."""
    return reply_with_headers(rig, dmarc_pass_for("evil.example") + from_lines)


@pytest.mark.parametrize(
    ("from_lines", "rule"),
    [
        (f"From: {MANAGER}, attacker@evil.example\r\n", "answer_ignored:sender_ambiguous"),
        (
            f"From: {MANAGER}\r\nFrom: attacker@evil.example\r\n",
            "answer_ignored:sender_ambiguous",
        ),
        (f"From: {MANAGER}\r\n", "answer_ignored:manager_dmarc_for_another_domain"),
    ],
    ids=["two-addresses", "two-from-headers", "pass-for-another-domain"],
)
def test_a_dmarc_pass_for_another_from_than_the_managers_is_ignored(
    rig: ApprovalRig, from_lines: str, rule: str
) -> None:
    opened(rig)
    forged = forged_by_header_differential(rig, from_lines)

    rig.tick()

    assert_nothing_executed(rig)
    assert only_line_about(rig, forged).rules == (rule,)


# --- fix round 1 ---

# C1: a From that Python reduces to the manager's address, sent from the manager's own domain.

DEFECTIVE_MANAGER_FROM_LINES = [
    f"From: {MANAGER} <attacker@example.com>\r\n",
    f"From: <{MANAGER}> attacker@example.com\r\n",
    f"From: {MANAGER} (Boss) <attacker@example.com>\r\n",
    f"From: Boss <{MANAGER}>attacker@example.com\r\n",
    f"From: {MANAGER};attacker@example.com\r\n",
    f"From: {MANAGER}\r\n\t<attacker@example.com>\r\n",
]


@pytest.mark.parametrize("from_lines", DEFECTIVE_MANAGER_FROM_LINES)
def test_a_from_that_parses_with_defects_is_ignored_even_from_the_managers_domain(
    rig: ApprovalRig, from_lines: str
) -> None:
    opened(rig)
    forged = reply_with_headers(rig, dmarc_pass_for("example.com") + from_lines)

    rig.tick()

    assert_nothing_executed(rig)
    line = only_line_about(rig, forged)
    assert (line.outcome, line.rules) == ("ignored", ("answer_ignored:sender_ambiguous",))


PROTON = "ionel.cimpan@protonmail.com"
DAY1_PROTON_RESULT = (
    "Authentication-Results: mx.google.com;\r\n"
    "       dkim=pass header.i=@protonmail.com header.s=protonmail3 header.b=AbCdEf12;\r\n"
    "       spf=pass (google.com: domain of ionel.cimpan@protonmail.com designates"
    " 185.70.43.17 as permitted sender) smtp.mailfrom=ionel.cimpan@protonmail.com;\r\n"
    "       dmarc=pass (p=QUARANTINE sp=QUARANTINE dis=NONE) header.from=protonmail.com\r\n"
)


def test_the_day1_proton_reply_still_counts(tmp_path: Path) -> None:
    rig = opened(rig_with(tmp_path, manager_address=PROTON))
    from_line = f"From: =?utf-8?Q?Ionel_C=C3=AEmpan?= <{PROTON}>\r\n"
    reply_with_headers(rig, DAY1_PROTON_RESULT + from_line)

    rig.tick()

    assert_executed_once(rig)


# I1: once a successor exists, the supersede sticks.


def free_the_slot(rig: ApprovalRig) -> None:
    rig.calendar.add(event_at("someone-elses-meeting", cancelled=True))


def test_an_interrupted_supersede_never_lets_the_original_execute(rig: ApprovalRig) -> None:
    rig.receive_meeting_request(
        decision_of(
            Label.MEETING_REQUEST,
            meeting=MEETING,
            urgent=True,
            urgent_quote="today",
            urgent_draft=URGENT_DRAFT,
        )
    )
    rig.calendar.add(event_at("someone-elses-meeting"))
    rig.answer("APPROVE")
    rig.mailbox.send_failures = [SystemWideError("Gmail is down", next_action="wait")]
    with pytest.raises(SystemWideError):
        rig.service.process_all()
    free_the_slot(rig)

    rig.tick()
    rig.tick()
    rig.answer("APPROVE", thread_id=rig.request_thread(1))
    rig.tick()

    assert rig.approval(0).state is ApprovalState.SUPERSEDED
    assert rig.approval(1).state is ApprovalState.EXECUTED
    assert rig.calendar.inserted == []
    assert "confirmation" not in rig.kinds_sent()
    assert rig.kinds_sent().count("urgent_reply") == 0
    assert rig.kinds_sent().count("alternatives") == 1
    assert len(rig.requests_sent()) == 2


def test_a_successor_already_open_makes_the_supersede_stick(rig: ApprovalRig) -> None:
    opened(rig)
    source = extract_facts(rig.mailbox.get(SOURCE_ID), rig.service_fact_settings())
    later = Interval(TUESDAY_SLOT.start + timedelta(days=1), TUESDAY_SLOT.end + timedelta(days=1))
    rig.service.open(
        OpenApproval(
            source=source,
            actions=(alternatives_reply(SOURCE_ID, (later,), attempt=1),),
            holds=(later,),
            summary="Wants a sync.",
            attempt=1,
        )
    )
    answer = rig.answer("APPROVE")

    rig.service.process_all()

    assert rig.approval(0).state is ApprovalState.SUPERSEDED
    assert rig.approval(1).state is ApprovalState.AWAITING
    assert rig.calendar.inserted == []
    assert "confirmation" not in rig.kinds_sent()
    assert only_line_about(rig, answer).rules == ("slot_taken",)
    assert {hold.approval_id for hold in rig.store.holds_active(rig.inbox.clock.now())} == {
        rig.approval(1).id
    }


# X2: an answer whose source email can't be read ends, after two retries, as failed.


def test_an_approve_whose_source_email_cannot_be_read_fails_after_two_retries(
    rig: ApprovalRig,
) -> None:
    opened(rig)
    rig.calendar.add(event_at("someone-elses-meeting"))
    rig.mailbox.get_failures[SOURCE_ID] = EmailError("no such message", next_action="none")
    approve = rig.answer("APPROVE")
    reject = rig.answer("REJECT")

    rig.service.process_all()
    rig.service.process_all()
    after_two = (rig.approval().state, lines_about(rig, reject))
    rig.service.process_all()

    assert after_two == (ApprovalState.AWAITING, [])
    assert rig.approval().state is ApprovalState.FAILED
    assert [line.outcome for line in lines_about(rig, approve)] == ["failed"] * 3
    assert rig.store.holds_active(rig.inbox.clock.now()) == []
    assert only_line_about(rig, reject).rules == ("answer_after_the_decision",)
    assert rig.store.approval_get(approval_id(SOURCE_ID, 1)) is None


# Late answers: a request thread the store has no awaiting approval for.


def test_an_answer_in_a_request_thread_with_no_approval_left_is_labelled_and_audited_once(
    rig: ApprovalRig,
) -> None:
    opened(rig)
    thread = rig.request_thread()
    decided_before = list(rig.inbox.decided)
    rig.restart_with_the_volume_wiped()
    answer = rig.answer("APPROVE", thread_id=thread)

    for _ in range(3):
        rig.tick()

    assert PROCESSED in rig.mailbox.labels_of(answer)
    assert rig.mailbox.get_log.count(answer) == 1
    line = only_line_about(rig, answer)
    assert (line.outcome, line.rules, line.approval_id) == ("ignored", ("late_answer",), None)
    assert rig.inbox.decided == decided_before
    assert rig.calendar.inserted == []


# Bottom-posted answers, under an attribution line.


@pytest.mark.parametrize(
    ("text", "state"),
    [
        (
            "On Sat, 7 Nov 2026 at 10:00, AECA <aeca@gmail.com> wrote:\n"
            "> Hello,\n> To answer, reply with APPROVE or REJECT\n\nAPPROVE",
            ApprovalState.EXECUTED,
        ),
        (
            "On Sat, Nov 7, 2026 at 10:00 AM AECA <aeca@gmail.com>\nwrote:\n\n> Hello,\n\nReject.",
            ApprovalState.REJECTED,
        ),
    ],
    ids=["approve", "reject-gmail-wrapped"],
)
def test_a_bottom_posted_answer_under_an_attribution_line_is_read(
    rig: ApprovalRig, text: str, state: ApprovalState
) -> None:
    opened(rig)
    rig.answer(text)

    rig.service.process_all()

    assert rig.approval().state is state


# What the spec says the texts hold.


def test_the_request_for_an_urgent_internal_email_says_why_it_is_urgent(rig: ApprovalRig) -> None:
    rig.receive_meeting_request(
        decision_of(
            Label.TASK, urgent=True, urgent_quote="the server is down", urgent_draft=URGENT_DRAFT
        )
    )

    [request] = rig.requests_sent()
    assert 'Why it is urgent: "the server is down"' in request.body


def test_the_event_description_holds_the_quoted_times(rig: ApprovalRig) -> None:
    opened(rig)
    rig.answer("APPROVE")

    rig.service.process_all()

    [event] = rig.calendar.inserted
    for text in ("Tuesday at 3pm", COLLEAGUE, "Sync?", rig.approval().id):
        assert text in event.description


# Ruling R42: the evaluators write from Germany.


def test_a_manager_reply_with_a_raw_utf8_display_name_counts(rig: ApprovalRig) -> None:
    opened(rig)
    reply_with_headers(rig, dmarc_pass_for("example.com") + f"From: Jürgen Müller <{MANAGER}>\r\n")

    rig.tick()

    assert_executed_once(rig)


def test_a_raw_utf8_display_name_does_not_excuse_a_structural_defect(rig: ApprovalRig) -> None:
    opened(rig)
    forged = reply_with_headers(
        rig, dmarc_pass_for("example.com") + f"From: Jürgen <{MANAGER}> attacker@example.com\r\n"
    )

    rig.tick()

    assert_nothing_executed(rig)
    assert only_line_about(rig, forged).rules == ("answer_ignored:sender_ambiguous",)


# --- fix round 2 ---


@pytest.mark.parametrize(
    ("text", "state"),
    [
        (
            "Reject \u2014 as the client wrote:\n> Tuesday at 3pm?\n\n"
            "Approved only if they move it to Wednesday.\n",
            ApprovalState.AWAITING,
        ),
        (
            "On Sat, Nov 7, 2026 at 10:00 AM AECA <aeca@gmail.com> wrote:\n"
            "> If you approve, AECA will:\n"
            '> 1. create the calendar event "Sync"\n'
            "Approve this one.\n"
            "> 2. send this reply:\n"
            "> | Hello, confirmed for Tuesday.\n"
            "Reject: do not send that text.\n",
            ApprovalState.AWAITING,
        ),
    ],
    ids=["reject-above-a-quote", "inline-per-step"],
)
def test_the_rereviews_replies_are_never_read_as_approve(
    rig: ApprovalRig, text: str, state: ApprovalState
) -> None:
    opened(rig)
    rig.answer(text)

    rig.service.process_all()

    assert rig.approval().state is state
    assert rig.calendar.inserted == []
    assert "confirmation" not in rig.kinds_sent()


# R43: message-level shapes Python reduces to one clean From.

MESSAGE_LEVEL_FORGERIES = [
    f"From : attacker@example.com\r\nFrom: {MANAGER}\r\n",
    f"From: {MANAGER}\r\nFrom : attacker@example.com\r\n",
    f"From: {MANAGER}\rX-y: <attacker@example.com>\r\n",
    f"From: {MANAGER}\r<attacker@example.com>\r\n",
    f"From: {MANAGER}\n<attacker@example.com>\r\n",
]


@pytest.mark.parametrize(
    "from_lines",
    MESSAGE_LEVEL_FORGERIES,
    ids=[
        "from-space-colon-first",
        "from-space-colon-last",
        "bare-cr-header-tail",
        "bare-cr",
        "bare-lf",
    ],
)
def test_a_message_level_header_defect_is_ignored_even_from_the_managers_domain(
    rig: ApprovalRig, from_lines: str
) -> None:
    opened(rig)
    forged = reply_with_headers(rig, dmarc_pass_for("example.com") + from_lines)

    rig.tick()

    assert_nothing_executed(rig)
    assert only_line_about(rig, forged).rules == ("answer_ignored:sender_ambiguous",)


# Ruling R44: a disclaimer the mail server appends below the quote does not stop an approval.


def test_a_top_posted_approve_with_a_disclaimer_below_the_quote_executes(rig: ApprovalRig) -> None:
    opened(rig)
    rig.answer(
        "APPROVE\n\nOn Sat, Nov 7, 2026 at 10:00 AM AECA <aeca@gmail.com> wrote:\n"
        "> Hello,\n> To answer, reply with APPROVE or REJECT as the first word\n\n"
        "This e-mail is confidential. If you are not the intended recipient, delete it.\n"
        "Deutsche Example AG, Registered office: Bonn.\n"
    )

    rig.service.process_all()

    assert_executed_once(rig)


# Fix round 3: a `From :` line as the last header of a multipart reply executed with a same-domain
# pass, since Python moves it into the preamble without a defect.


def reply_with_raw_text(rig: ApprovalRig, text: str) -> str:
    rig.mailbox.add(
        RawEmail(
            message_id="raw-text",
            thread_id=rig.request_thread(),
            label_ids=frozenset({"INBOX"}),
            rfc822=text.encode(),
            internal_date=datetime(2026, 11, 7, 11, 0, tzinfo=BUCHAREST),
        )
    )
    return "raw-text"


def multipart_reply(from_lines: str, last_header: str) -> str:
    return (
        dmarc_pass_for("example.com")
        + from_lines
        + f"To: {ACCOUNT}\r\nSubject: Re: [AECA] Approve: meeting\r\n"
        + 'MIME-Version: 1.0\r\nContent-Type: multipart/alternative; boundary="b"\r\n'
        + f"{last_header}\r\n\r\n"
        + "--b\r\nContent-Type: text/plain; charset=utf-8\r\n\r\nAPPROVE\r\n"
        + "--b\r\nContent-Type: text/html; charset=utf-8\r\n\r\n<div>APPROVE</div>\r\n--b--\r\n"
    )


@pytest.mark.parametrize(
    "last_header",
    ["From : attacker@example.com", "FROM : attacker@example.com", "From\t: attacker@example.com"],
    ids=["from-space-colon", "uppercase", "tab"],
)
def test_a_second_from_as_the_last_header_of_a_multipart_reply_is_ignored(
    rig: ApprovalRig, last_header: str
) -> None:
    opened(rig)
    forged = reply_with_raw_text(rig, multipart_reply(f"From: {MANAGER}\r\n", last_header))

    rig.tick()

    assert_nothing_executed(rig)
    assert only_line_about(rig, forged).rules == ("answer_ignored:sender_ambiguous",)


def test_a_clean_multipart_proton_shaped_reply_still_counts(tmp_path: Path) -> None:
    rig = opened(rig_with(tmp_path, manager_address=PROTON))
    from_line = f"From: =?utf-8?Q?Ionel_C=C3=AEmpan?= <{PROTON}>\r\n"
    reply = multipart_reply(from_line, "X-Pm-Origin: internal")
    reply_with_raw_text(rig, DAY1_PROTON_RESULT + reply.split("\r\n", 1)[1])

    rig.tick()

    assert_executed_once(rig)
