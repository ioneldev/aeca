"""The full flow for one inbox email: decide, plan, journal, act once, ask, audit, mark processed.

The spec's blocking cases 1, 7 and 8 (§5) are here. A kill is a Killed raised by a fake right after
a Google write: no recovery code runs, exactly as when the process dies.
"""

from dataclasses import replace
from datetime import datetime
from pathlib import Path

import pytest
from fakes.crash import Killed
from fakes.recording_approvals import FAKE_APPROVAL_ID
from support.decisions import SUMMARY, decision_of
from support.facts import NOW, make_facts
from support.inbox_rig import ACCOUNT, BUCHAREST, InboxRig, RigOptions

from aeca.application.ports import OpenApproval
from aeca.domain.actions import ActionRecord, ActionStatus, Verb
from aeca.domain.approval import ApprovalState
from aeca.domain.calendar_event import CalendarEvent
from aeca.domain.decision import (
    Decision,
    Meeting,
    ModelDecision,
    ModelMeeting,
    ModelWindow,
    Window,
)
from aeca.domain.errors import EmailError
from aeca.domain.ids import approval_id
from aeca.domain.labels import AWAITING, ERROR, PROCESSED, Label, ReplyKind
from aeca.domain.time_range import Interval
from aeca.domain.validation import ValidationContext, validate

COLLEAGUE = "colleague@example.com"
MANAGER = "boss@example.com"
NEWSLETTER = "news@shop.example"
TUESDAY_3PM = datetime(2026, 11, 10, 15, 0, tzinfo=BUCHAREST)
TUESDAY_SLOT = Interval(TUESDAY_3PM, datetime(2026, 11, 10, 15, 30, tzinfo=BUCHAREST))
MEETING = Meeting(
    title="Sync", duration_minutes=None, windows=(Window(TUESDAY_3PM, None, "Tuesday at 3pm"),)
)
INVITATION_UID = "invite-1@example.com"
INVITATION = (
    f"BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nUID:{INVITATION_UID}\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n"
)
ACKNOWLEDGED_TASK = decision_of(Label.TASK, reply=ReplyKind.ACKNOWLEDGEMENT)
ACKNOWLEDGED_MARKETING = decision_of(Label.MARKETING, reply=ReplyKind.ACKNOWLEDGEMENT)
URGENT_TASK = decision_of(Label.TASK, urgent=True, urgent_quote="due today")


@pytest.fixture
def rig(tmp_path: Path) -> InboxRig:
    return InboxRig(tmp_path)


def rig_with(state_dir: Path, **options: object) -> InboxRig:
    return InboxRig(state_dir, RigOptions(**options))  # type: ignore[arg-type]


def receive_meeting_request(rig: InboxRig) -> str:
    rig.next_decision = decision_of(Label.MEETING_REQUEST, meeting=MEETING)
    return rig.receive(sender=COLLEAGUE, subject="Sync?", body="Tuesday at 3pm?")


def receive_urgent_task(rig: InboxRig) -> str:
    rig.next_decision = URGENT_TASK
    return rig.receive(sender=COLLEAGUE, subject="Report", body="The report is due today")


def kinds_sent(rig: InboxRig) -> list[str]:
    return [message.kind for message in rig.mailbox.sent]


def records_of(rig: InboxRig, message_id: str) -> list[ActionRecord]:
    entry = rig.store.journal_get(message_id)
    assert entry is not None
    records = [rig.store.action_get(action.key) for action in entry.actions]
    return [record for record in records if record is not None]


# --- the journal comes first (spec §4.6) ---


def test_the_journal_is_recorded_before_the_first_side_effect(rig: InboxRig) -> None:
    rig.next_decision = ACKNOWLEDGED_MARKETING
    message_id = rig.receive(sender=NEWSLETTER)
    rig.mailbox.modify_failures.append(Killed())

    with pytest.raises(Killed):
        rig.processor.process(message_id)

    assert rig.mailbox.modify_log[0].add == ("MARKETING",)
    assert [record.action.verb for record in records_of(rig, message_id)] == [
        Verb.APPLY_LABEL, Verb.SEND_REPLY, Verb.ARCHIVE,
    ]  # fmt: skip
    assert {record.status for record in records_of(rig, message_id)} == {ActionStatus.PENDING}


def test_a_first_modify_that_fails_fails_the_email_and_keeps_the_journal(rig: InboxRig) -> None:
    message_id = rig.receive()
    rig.mailbox.modify_failures.append(EmailError("Gmail refused", next_action="none"))

    line = rig.processor.process(message_id)

    assert line is not None and line.outcome == "failed"
    assert rig.store.journal_get(message_id) is not None


# --- blocking case 1: a crash between a Google write and the state write ---


@pytest.mark.parametrize("volume", ["same-volume", "volume-wiped"])
def test_a_reply_is_sent_once_after_a_crash_between_send_and_state_write(
    rig: InboxRig, volume: str
) -> None:
    rig.next_decision = ACKNOWLEDGED_TASK
    message_id = rig.receive(sender=COLLEAGUE)
    rig.mailbox.kill_after_entry = "send:acknowledgement"

    with pytest.raises(Killed):
        rig.processor.process(message_id)
    if volume == "same-volume":
        rig.restart()
    else:
        rig.restart_with_the_volume_wiped()
    line = rig.processor.process(message_id)

    assert kinds_sent(rig) == ["acknowledgement"]
    assert line is not None and line.outcome == "done"
    assert PROCESSED in rig.mailbox.labels_of(message_id)


def test_a_recorded_draft_that_still_exists_is_sent_on_restart(rig: InboxRig) -> None:
    rig.next_decision = ACKNOWLEDGED_TASK
    message_id = rig.receive(sender=COLLEAGUE)
    rig.mailbox.send_failures.append(Killed())

    with pytest.raises(Killed):
        rig.processor.process(message_id)
    rig.restart()
    rig.processor.process(message_id)

    assert rig.journal.count("draft:acknowledgement") == 1
    assert kinds_sent(rig) == ["acknowledgement"]
    assert rig.mailbox.drafts == {}


def test_a_recorded_draft_that_is_gone_counts_as_sent(tmp_path: Path) -> None:
    rig = rig_with(tmp_path, manager_address=MANAGER)
    message_id = receive_urgent_task(rig)
    rig.mailbox.kill_after_entry = "send:urgent-notice"

    with pytest.raises(Killed):
        rig.processor.process(message_id)
    rig.restart()
    line = rig.processor.process(message_id)

    assert [message.kind for message in rig.mailbox.sent_to(MANAGER)] == ["urgent-notice"]
    assert line is not None and ("notify_manager", "", "done") in line.actions


# The Google writes of an urgent, acknowledged TASK: its label, the acknowledgement's draft, send
# and AECA/processed, the star, URGENT, the notice's draft, send and AECA/processed, and the
# email's own AECA/processed.
WRITES_OF_AN_URGENT_ACKNOWLEDGED_TASK = 10
URGENT_NOTICE_WRITES = 7


def test_the_kill_points_cover_every_google_write_of_the_flow(tmp_path: Path) -> None:
    assert writes_in_a_full_run(tmp_path) == WRITES_OF_AN_URGENT_ACKNOWLEDGED_TASK


@pytest.mark.parametrize("kill_after", range(1, WRITES_OF_AN_URGENT_ACKNOWLEDGED_TASK + 1))
def test_every_kill_point_leaves_each_send_done_exactly_once_after_a_restart(
    tmp_path: Path, kill_after: int
) -> None:
    rig = rig_with(tmp_path, manager_address=MANAGER)
    message_id = receive_urgent_acknowledged_task(rig)
    rig.mailbox.kill_after_writes = kill_after

    with pytest.raises(Killed):
        rig.processor.process(message_id)
    rig.restart()
    rig.processor.process(message_id)

    assert sorted(kinds_sent(rig)) == ["acknowledgement", "urgent-notice"]
    assert PROCESSED in rig.mailbox.labels_of(message_id)


@pytest.mark.parametrize("kill_after", range(1, URGENT_NOTICE_WRITES + 1))
def test_a_wiped_volume_after_any_kill_sends_the_urgent_notice_once(
    tmp_path: Path, kill_after: int
) -> None:
    rig = rig_with(tmp_path, manager_address=MANAGER)
    message_id = receive_urgent_task(rig)
    rig.mailbox.kill_after_writes = kill_after

    with pytest.raises(Killed):
        rig.processor.process(message_id)
    rig.restart_with_the_volume_wiped()
    rig.processor.process(message_id)

    assert [message.kind for message in rig.mailbox.sent_to(MANAGER)] == ["urgent-notice"]


@pytest.mark.parametrize("kill_after", [1, 2, 3, 4, 5, 6])
def test_a_wiped_volume_after_any_kill_still_sends_the_reply_once(
    rig: InboxRig, kill_after: int
) -> None:
    rig.next_decision = ACKNOWLEDGED_MARKETING
    message_id = rig.receive(sender=NEWSLETTER)
    rig.mailbox.kill_after_writes = kill_after

    with pytest.raises(Killed):
        rig.processor.process(message_id)
    rig.restart_with_the_volume_wiped()
    rig.processor.process(message_id)

    assert kinds_sent(rig) == ["acknowledgement"]


@pytest.mark.parametrize("kill_after_entry", ["modify:MEETING_REQUEST", f"modify:{AWAITING}"])
def test_an_approval_due_when_aeca_was_killed_is_opened_once_after_the_restart(
    rig: InboxRig, kill_after_entry: str
) -> None:
    message_id = receive_meeting_request(rig)
    rig.mailbox.kill_after_entry = kill_after_entry

    with pytest.raises(Killed):
        rig.processor.process(message_id)
    rig.restart()
    line = rig.processor.process(message_id)

    assert [approval.id for approval in rig.store.approvals_awaiting()] == [
        approval_id(message_id, 0)
    ]
    assert len(rig.tracer.of_kind("approval_request_deferred")) == 1
    assert line is not None and line.approval_id == approval_id(message_id, 0)


def writes_in_a_full_run(state_dir: Path) -> int:
    rig = rig_with(state_dir, manager_address=MANAGER)
    rig.processor.process(receive_urgent_acknowledged_task(rig))
    return sum(entry.startswith(("modify:", "draft:", "send:")) for entry in rig.journal)


def receive_urgent_acknowledged_task(rig: InboxRig) -> str:
    message_id = receive_urgent_task(rig)
    rig.next_decision = replace(URGENT_TASK, reply=ReplyKind.ACKNOWLEDGEMENT)
    return message_id


# --- blocking case 7: one message, never its thread ---


@pytest.mark.parametrize(
    ("label", "verb", "change"),
    [
        (Label.MARKETING, "archive", ((), ("INBOX",))),
        (Label.SPAM, "move_to_spam", (("SPAM",), ())),
    ],
)
def test_archiving_one_message_leaves_its_thread_sibling_in_the_inbox(
    rig: InboxRig, label: Label, verb: str, change: tuple[tuple[str, ...], tuple[str, ...]]
) -> None:
    sibling = rig.receive(message_id="a", thread_id="shared", subject="Offer")
    target = rig.receive(message_id="b", thread_id="shared", subject="Offer")
    rig.next_decision = decision_of(label)

    line = rig.processor.process(target)

    assert line is not None and (verb, "", "done") in line.actions
    assert change in {(call.add, call.remove) for call in rig.mailbox.modify_log}
    assert {call.message_id for call in rig.mailbox.modify_log} == {target}
    assert rig.mailbox.labels_of(sibling) == {"INBOX"}


# --- blocking case 8: replies AECA must not send ---


def test_no_unapproved_reply_to_self_or_dmarc_fail_and_an_out_of_office_gets_one_reply_at_most(
    tmp_path: Path,
) -> None:
    assert replies_to_the_account_itself(InboxRig(tmp_path / "self")) == []
    assert replies_to_a_dmarc_failure(InboxRig(tmp_path / "dmarc")) == []
    assert replies_to_an_out_of_office_loop(InboxRig(tmp_path / "ooo")) == ["thank_you"]


def replies_to_the_account_itself(rig: InboxRig) -> list[str]:
    rig.next_decision = ACKNOWLEDGED_TASK
    message_id = rig.receive(sender=ACCOUNT, label_ids=("INBOX", "SENT"))

    line = rig.processor.process(message_id)

    assert line is not None and line.outcome == "done"
    return kinds_sent(rig)


def replies_to_a_dmarc_failure(rig: InboxRig) -> list[str]:
    rig.next_decision = ACKNOWLEDGED_TASK
    message_id = rig.receive(sender="ceo@company.example", dmarc="fail")

    line = rig.processor.process(message_id)

    assert line is not None and line.outcome == "done"
    return kinds_sent(rig)


def replies_to_an_out_of_office_loop(rig: InboxRig) -> list[str]:
    """An auto-responder with no auto-reply headers answers each of AECA's replies in turn."""
    rig.next_decision = decision_of(Label.INFO_ONLY, reply=ReplyKind.THANK_YOU)
    for turn in range(3):
        message_id = rig.receive(
            message_id=f"ooo-{turn}",
            thread_id="ooo-thread",
            sender="away@example.com",
            subject="Out of office",
            body="I am away until Monday.",
        )
        rig.processor.process(message_id)
    return [message.kind for message in rig.mailbox.sent_to("away@example.com")]


@pytest.mark.parametrize("approved_kind", ["confirmation", "approved-decline"])
def test_an_approved_kind_in_the_thread_does_not_use_up_the_unapproved_reply(
    rig: InboxRig, approved_kind: str
) -> None:
    rig.receive(
        message_id="approved",
        thread_id="t",
        sender=ACCOUNT,
        headers={"X-AECA-Kind": approved_kind, "X-AECA-Id": "k-0000000000000001"},
        label_ids=("SENT",),
    )
    rig.next_decision = ACKNOWLEDGED_TASK
    message_id = rig.receive(message_id="thanks", thread_id="t", sender=COLLEAGUE)

    rig.processor.process(message_id)

    assert kinds_sent(rig) == ["acknowledgement"]


def test_an_automatic_decline_in_the_thread_uses_up_the_unapproved_reply(rig: InboxRig) -> None:
    rig.receive(
        message_id="request",
        thread_id="t",
        sender=ACCOUNT,
        headers={"X-AECA-Kind": "decline", "X-AECA-Id": "k-0000000000000001"},
        label_ids=("SENT",),
    )
    rig.next_decision = ACKNOWLEDGED_TASK
    message_id = rig.receive(message_id="thanks", thread_id="t", sender=COLLEAGUE)

    rig.processor.process(message_id)

    assert kinds_sent(rig) == []


# --- resuming (spec §4.6) ---


def test_unfinished_work_resumes_by_message_id_after_the_email_was_archived(
    rig: InboxRig,
) -> None:
    rig.next_decision = decision_of(Label.MARKETING)
    message_id = rig.receive(sender=NEWSLETTER)
    rig.mailbox.kill_after_entry = "modify:-INBOX"

    with pytest.raises(Killed):
        rig.processor.process(message_id)
    rig.restart()

    assert message_id not in rig.mailbox.unprocessed_inbox(50)
    assert rig.processor.unfinished() == [message_id]
    line = rig.processor.process(message_id)
    assert line is not None
    assert line.actions == (("apply_label", "MARKETING", "done"), ("archive", "", "done"))
    assert PROCESSED in rig.mailbox.labels_of(message_id)
    assert rig.processor.unfinished() == []


def test_a_resume_reuses_the_journalled_decision_without_asking_the_model_again(
    rig: InboxRig,
) -> None:
    rig.next_decision = ACKNOWLEDGED_TASK
    message_id = rig.receive(sender=COLLEAGUE)
    rig.mailbox.kill_after_entry = "modify:TASK"

    with pytest.raises(Killed):
        rig.processor.process(message_id)
    rig.restart()
    rig.next_decision = decision_of(Label.SPAM)
    line = rig.processor.process(message_id)

    assert len(rig.decided) == 1
    assert line is not None and line.label == "TASK"
    assert kinds_sent(rig) == ["acknowledgement"]


def test_an_approved_approvals_actions_are_not_resumed_by_the_inbox(rig: InboxRig) -> None:
    rig.processor.process(receive_meeting_request(rig))
    [approval] = rig.store.approvals_awaiting()

    rig.store.approval_save(replace(approval, state=ApprovalState.APPROVED))

    assert rig.processor.unfinished() == []


def test_an_email_given_up_after_three_tries_is_not_resumed_again(rig: InboxRig) -> None:
    rig.next_decision = ACKNOWLEDGED_MARKETING
    message_id = rig.receive(sender=NEWSLETTER)
    for _ in range(3):
        rig.mailbox.send_failures.append(EmailError("Gmail refused", next_action="none"))
        rig.processor.process(message_id)

    assert rig.mailbox.labels_of(message_id) >= {ERROR, PROCESSED}
    assert rig.processor.unfinished() == []


def test_a_resume_never_reopens_an_approval_the_manager_already_answered(rig: InboxRig) -> None:
    message_id = receive_meeting_request(rig)
    rig.mailbox.kill_after_entry = f"modify:{AWAITING}"
    with pytest.raises(Killed):
        rig.processor.process(message_id)
    [approval] = rig.store.approvals_awaiting()
    rig.store.approval_save(replace(approval, state=ApprovalState.REJECTED))
    rig.restart()

    line = rig.processor.process(message_id)

    assert line is not None and (line.outcome, line.approval_state) == ("done", "rejected")
    stored = rig.store.approval_get(approval.id)
    assert stored is not None and stored.state is ApprovalState.REJECTED


# --- meetings ---


def test_an_invitation_already_on_the_calendar_is_label_only(rig: InboxRig) -> None:
    rig.calendar.add(
        CalendarEvent(
            id="ev-1",
            interval=TUESDAY_SLOT,
            all_day=False,
            cancelled=False,
            declined_by_me=False,
            ical_uid=INVITATION_UID,
        )
    )
    rig.next_decision = decision_of(Label.MEETING_REQUEST, meeting=MEETING)
    message_id = rig.receive(sender=COLLEAGUE, body="Tuesday at 3pm?", calendar=INVITATION)

    line = rig.processor.process(message_id)

    assert line is not None
    assert line.actions == (("apply_label", "MEETING_REQUEST", "done"),)
    assert "invitation_on_calendar" in line.rules
    assert (line.outcome, line.approval_id) == ("done", None)
    assert rig.store.approvals_awaiting() == []


def test_awaiting_then_processed_when_an_approval_is_opened(rig: InboxRig) -> None:
    opener = rig.record_approvals()
    message_id = receive_meeting_request(rig)

    line = rig.processor.process(message_id)

    [request] = opener.opened
    assert request.source.message_id == message_id
    assert [(a.verb, a.args.get("template")) for a in request.actions] == [
        (Verb.CREATE_EVENT, None), (Verb.SEND_REPLY, "confirmation"),
    ]  # fmt: skip
    assert (request.holds, request.summary, request.attempt) == ((TUESDAY_SLOT,), SUMMARY, 0)
    assert rig.journal == [
        "modify:MEETING_REQUEST", "approval", f"modify:{AWAITING}", "audit", f"modify:{PROCESSED}",
    ]  # fmt: skip
    assert line is not None
    assert (line.outcome, line.approval_id, line.approval_state) == (
        "awaiting_approval", FAKE_APPROVAL_ID, "awaiting",
    )  # fmt: skip


def test_the_deferred_opener_saves_the_approval_and_the_email_waits_for_it(
    rig: InboxRig,
) -> None:
    message_id = receive_meeting_request(rig)

    line = rig.processor.process(message_id)

    [approval] = rig.store.approvals_awaiting()
    assert (approval.id, approval.holds, approval.summary) == (
        approval_id(message_id, 0), (TUESDAY_SLOT,), SUMMARY,
    )  # fmt: skip
    assert rig.mailbox.sent == []
    assert rig.mailbox.labels_of(message_id) >= {"MEETING_REQUEST", AWAITING, PROCESSED}
    assert line is not None
    assert ("create_event", "", "pending") in line.actions
    assert ("send_reply", "confirmation", "pending") in line.actions


def test_an_approval_is_opened_before_the_email_leaves_the_inbox(tmp_path: Path) -> None:
    rig = rig_with(tmp_path, sales_decline_needs_approval=True)
    rig.next_decision = decision_of(Label.SALES_OUTREACH)
    message_id = rig.receive(sender="rep@vendor.example")

    rig.processor.process(message_id)

    assert [entry for entry in rig.journal if entry != "audit"] == [
        "modify:SALES_OUTREACH", f"modify:{AWAITING}", "modify:-INBOX", f"modify:{PROCESSED}",
    ]  # fmt: skip


def test_a_gated_action_is_never_run_by_the_inbox(rig: InboxRig) -> None:
    message_id = receive_meeting_request(rig)

    rig.processor.process(message_id)

    assert [record.action.verb for record in records_of(rig, message_id)] == [Verb.APPLY_LABEL]
    assert rig.calendar.inserted == []
    assert rig.mailbox.sent == []


# --- the urgent notice ---


def test_an_urgent_internal_email_without_a_draft_notifies_the_manager_in_a_new_thread(
    tmp_path: Path,
) -> None:
    rig = rig_with(tmp_path, internal_senders=frozenset({COLLEAGUE}), manager_address=MANAGER)
    message_id = receive_urgent_task(rig)

    rig.processor.process(message_id)

    [notice] = rig.mailbox.sent_to(MANAGER)
    notify = next(r for r in records_of(rig, message_id) if r.action.verb is Verb.NOTIFY_MANAGER)
    assert (notice.kind, notice.aeca_id, notice.thread_id, notice.in_reply_to) == (
        "urgent-notice", notify.action.key, None, None,
    )  # fmt: skip
    assert all(text in notice.body for text in (COLLEAGUE, "Report", SUMMARY, "due today"))
    assert rig.mailbox.labels_of(message_id) >= {"TASK", "STARRED", "URGENT", PROCESSED}


def test_without_a_manager_address_the_notice_goes_to_the_account(rig: InboxRig) -> None:
    rig.processor.process(receive_urgent_task(rig))

    assert [message.kind for message in rig.mailbox.sent_to(ACCOUNT)] == ["urgent-notice"]


# --- the audit line ---


def test_the_audit_line_lists_each_action_with_its_status(rig: InboxRig) -> None:
    rig.next_decision = ACKNOWLEDGED_MARKETING
    done_id = rig.receive(message_id="done", thread_id="t1", sender=NEWSLETTER)
    failing_id = rig.receive(message_id="failing", thread_id="t2", sender=NEWSLETTER)

    done = rig.processor.process(done_id)
    rig.mailbox.send_failures.append(EmailError("Gmail refused the send", next_action="none"))
    failed = rig.processor.process(failing_id)

    assert done is not None and done.outcome == "done"
    assert done.actions == (
        ("apply_label", "MARKETING", "done"),
        ("send_reply", "acknowledgement", "done"),
        ("archive", "", "done"),
    )
    assert failed is not None and failed.outcome == "failed"
    assert failed.actions == (
        ("apply_label", "MARKETING", "done"),
        ("send_reply", "acknowledgement", "failed"),
        ("archive", "", "pending"),
    )
    assert failed.error is not None and "Gmail refused the send" in failed.error
    assert "INBOX" in rig.mailbox.labels_of(failing_id)


def test_a_failed_action_is_retried_on_the_next_poll_and_the_email_then_finishes(
    rig: InboxRig,
) -> None:
    rig.next_decision = ACKNOWLEDGED_TASK
    message_id = rig.receive(sender=COLLEAGUE)
    rig.mailbox.send_failures.append(EmailError("Gmail refused the send", next_action="none"))

    rig.processor.process(message_id)
    line = rig.processor.process(message_id)

    assert line is not None and line.outcome == "done"
    assert kinds_sent(rig) == ["acknowledgement"]
    assert rig.journal.count("draft:acknowledgement") == 1
    assert PROCESSED in rig.mailbox.labels_of(message_id)
    assert ERROR not in rig.mailbox.labels_of(message_id)


def test_the_audit_line_is_written_before_processed_which_comes_last(rig: InboxRig) -> None:
    message_id = rig.receive()

    rig.processor.process(message_id)

    assert rig.journal[-2:] == ["audit", f"modify:{PROCESSED}"]


def test_every_automatic_action_ends_done_in_the_store(rig: InboxRig) -> None:
    rig.next_decision = ACKNOWLEDGED_TASK
    message_id = rig.receive(sender=COLLEAGUE)

    rig.processor.process(message_id)

    assert {record.status for record in records_of(rig, message_id)} == {ActionStatus.DONE}


# --- what the inbox skips (spec §4.6, and the day-1 spikes) ---


def test_an_own_reply_that_came_back_with_inbox_is_marked_processed_without_a_model_call(
    rig: InboxRig,
) -> None:
    message_id = rig.receive(
        sender=ACCOUNT,
        headers={"X-AECA-Kind": "acknowledgement", "X-AECA-Id": "k-0123456789abcdef"},
        label_ids=("INBOX", "SENT"),
    )

    assert rig.processor.process(message_id) is None
    assert rig.decided == []
    assert rig.mailbox.added_labels(message_id) == [PROCESSED]


def test_a_manager_answer_in_an_approval_thread_is_left_for_the_approval_loop(
    rig: InboxRig,
) -> None:
    rig.processor.process(receive_meeting_request(rig))
    [approval] = rig.store.approvals_awaiting()
    rig.store.approval_save(replace(approval, request_thread_id="request-thread"))
    answer = rig.receive(message_id="answer", thread_id="request-thread", sender=MANAGER)

    assert rig.processor.process(answer) is None
    assert len(rig.decided) == 1
    assert rig.mailbox.added_labels(answer) == []


def test_a_manager_answer_in_a_request_thread_is_never_decided_even_with_the_store_wiped(
    rig: InboxRig,
) -> None:
    rig.receive(
        message_id="request",
        thread_id="request-thread",
        sender=ACCOUNT,
        to=MANAGER,
        subject="[AECA] Approve: reply to the client",
        headers={"X-AECA-Kind": "approval-request", "X-AECA-Id": "a-000000000001"},
        label_ids=("SENT",),
    )
    rig.restart_with_the_volume_wiped()
    rig.next_decision = ACKNOWLEDGED_TASK
    answer = rig.receive(
        message_id="answer",
        thread_id="request-thread",
        sender=MANAGER,
        subject="Re: [AECA] Approve: reply to the client",
        body="APPROVE",
    )

    assert rig.processor.process(answer) is None
    assert rig.decided == []
    assert rig.mailbox.sent == []
    # Task 13, fix round 1: no approval waits for it, so it is labelled and audited once.
    assert rig.mailbox.added_labels(answer) == [PROCESSED]
    [line] = rig.audit.lines
    assert (line.message_id, line.rules, line.outcome) == (answer, ("late_answer",), "ignored")


def test_a_thread_started_by_another_aeca_message_is_classified_as_usual(rig: InboxRig) -> None:
    rig.receive(
        message_id="notice",
        thread_id="notice-thread",
        sender=ACCOUNT,
        to=MANAGER,
        headers={"X-AECA-Kind": "urgent-notice", "X-AECA-Id": "k-0000000000000001"},
        label_ids=("SENT",),
    )
    answer = rig.receive(message_id="answer", thread_id="notice-thread", sender=MANAGER)

    assert rig.processor.process(answer) is not None
    assert len(rig.decided) == 1


def test_the_thread_is_read_once_and_before_the_model(rig: InboxRig) -> None:
    message_id = rig.receive(sender=COLLEAGUE)
    rig.mailbox.thread_failures.append(EmailError("Gmail refused", next_action="none"))

    failed = rig.processor.process(message_id)
    rig.mailbox.thread_reads.clear()
    done = rig.processor.process(message_id)

    assert failed is not None and failed.outcome == "failed"
    assert done is not None and done.outcome == "done"
    assert len(rig.decided) == 1
    assert rig.mailbox.thread_reads == ["thread-1"]


def test_an_opener_whose_approval_the_store_does_not_hold_fails_the_email(
    rig: InboxRig,
) -> None:
    class ForgetfulOpener:
        def open(self, request: OpenApproval) -> str:
            return "a-nowhere"

    rig.use_approvals(ForgetfulOpener())
    message_id = receive_meeting_request(rig)

    line = rig.processor.process(message_id)

    assert line is not None and line.outcome == "failed"
    assert line.error is not None and "a-nowhere" in line.error
    assert AWAITING not in rig.mailbox.labels_of(message_id)


def test_a_failed_line_keeps_the_approval_opened_before_the_failure(tmp_path: Path) -> None:
    rig = rig_with(tmp_path, sales_decline_needs_approval=True)
    rig.next_decision = decision_of(Label.SALES_OUTREACH)
    message_id = rig.receive(sender="rep@vendor.example")
    rig.mailbox.fail_next_modify(
        message_id, changing="INBOX", error=EmailError("Gmail refused", next_action="none")
    )

    line = rig.processor.process(message_id)

    assert line is not None and line.outcome == "failed"
    assert (line.approval_id, line.approval_state) == (approval_id(message_id, 0), "awaiting")


def test_an_email_found_processed_closes_its_journal(rig: InboxRig) -> None:
    message_id = rig.receive()
    rig.mailbox.kill_after_entry = f"modify:{PROCESSED}"
    with pytest.raises(Killed):
        rig.processor.process(message_id)
    rig.restart()
    resumable = rig.processor.unfinished()

    assert rig.processor.process(message_id) is None
    assert resumable == [message_id]
    assert rig.processor.unfinished() == []
    assert len(rig.audit.lines) == 1


def test_a_meeting_whose_only_window_spans_the_week_gets_alternatives_not_an_event(
    rig: InboxRig,
) -> None:
    opener = rig.record_approvals()
    rig.next_decision = validated_meeting_for_the_whole_week()
    message_id = rig.receive(sender=COLLEAGUE, subject="Sync?", body="Can we meet next week?")

    line = rig.processor.process(message_id)

    [request] = opener.opened
    assert [(a.verb, a.args.get("template")) for a in request.actions] == [
        (Verb.SEND_REPLY, "alternatives")
    ]
    assert line is not None and line.outcome == "awaiting_approval"


def validated_meeting_for_the_whole_week() -> Decision:
    submitted = ModelDecision(
        primary_label="MEETING_REQUEST",
        summary="a summary",
        meeting=ModelMeeting(
            title="Sync",
            windows=[
                ModelWindow(
                    start="2026-11-09T09:00:00+02:00",
                    end="2026-11-13T17:00:00+02:00",
                    quote="meet next week",
                )
            ],
        ),
    )
    context = ValidationContext(
        facts=make_facts(text="Can we meet next week?"), now=NOW, zone=BUCHAREST
    )
    result = validate(submitted, context)
    assert isinstance(result, Decision)
    return result
