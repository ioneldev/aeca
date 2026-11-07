"""The real processor inside the real service, with fakes only at the edges."""

from pathlib import Path

import pytest
from fakes.crash import Killed
from fakes.recording_console import RecordingConsole
from support.decisions import decision_of
from support.inbox_rig import InboxRig
from support.quiet_service import nothing_to_start, quiet_watch

from aeca.application.process_email import MAX_TRIES
from aeca.application.service import Service, ServiceDeps
from aeca.domain.approval import Approval, ApprovalState
from aeca.domain.audit import AuditLine
from aeca.domain.errors import EmailError, SystemWideError
from aeca.domain.labels import ERROR, PROCESSED, Label, ReplyKind

POISON_SUBJECT = "poison"


class NoApprovalLoop:
    """These ticks are about the inbox loop alone."""

    def process_all(self) -> list[AuditLine]:
        return []


MANAGER = "boss@example.com"


def service_of(rig: InboxRig) -> tuple[Service, RecordingConsole]:
    console = RecordingConsole()
    service = Service(
        ServiceDeps(
            processor=rig.processor,
            mailbox=rig.mailbox,
            console=console,
            poll_seconds=0.01,
            approvals=NoApprovalLoop(),
            watch=quiet_watch(),
            start_up=nothing_to_start,
        )
    )
    return service, console


def test_a_failing_email_is_retried_each_tick_while_the_good_one_after_it_is_done_once(
    tmp_path: Path,
) -> None:
    rig = InboxRig(tmp_path)
    rig.decisions_by_subject["fine"] = decision_of(Label.TASK)
    rig.next_decision = EmailError("cannot read this", next_action="none")
    rig.receive(message_id="bad", subject=POISON_SUBJECT)
    rig.receive(message_id="good", thread_id="thread-2", subject="fine")
    service, console = service_of(rig)

    results = [service.tick() for _ in range(MAX_TRIES + 1)]

    assert [(r.processed, r.failed) for r in results] == [(1, 1), (0, 1), (0, 1), (0, 0)]
    assert rig.mailbox.added_labels("bad") == [ERROR, ERROR, ERROR, PROCESSED]
    assert rig.mailbox.added_labels("good") == ["TASK", PROCESSED]
    assert [line.outcome for line in rig.audit.lines].count("failed") == MAX_TRIES
    assert len(console.emails) == MAX_TRIES + 1


def test_sixty_manager_answers_waiting_in_approval_threads_do_not_stop_a_new_email(
    tmp_path: Path,
) -> None:
    rig = InboxRig(tmp_path)
    for number in range(60):
        rig.store.approval_save(waiting_approval(number))
        rig.receive(message_id=f"answer-{number}", thread_id=f"request-{number}", sender=MANAGER)
    rig.receive(message_id="new", thread_id="new-thread", subject="Please review")
    service, _ = service_of(rig)

    result = service.tick()

    assert result.processed == 1
    assert PROCESSED in rig.mailbox.labels_of("new")
    assert [facts.message_id for facts in rig.decided] == ["new"]
    assert all(PROCESSED not in rig.mailbox.labels_of(f"answer-{n}") for n in range(60))


def test_unfinished_work_of_an_archived_email_is_finished_by_the_next_tick(
    tmp_path: Path,
) -> None:
    rig = InboxRig(tmp_path)
    rig.next_decision = decision_of(Label.MARKETING)
    rig.receive(message_id="newsletter", sender="news@shop.example")
    rig.mailbox.kill_after_entry = "modify:-INBOX"
    with pytest.raises(Killed):
        service_of(rig)[0].tick()
    rig.restart()
    service, console = service_of(rig)

    result = service.tick()

    assert result.processed == 1
    assert [line.message_id for line in console.emails] == ["newsletter"]
    assert PROCESSED in rig.mailbox.labels_of("newsletter")


def test_a_kill_after_the_archive_and_before_the_audit_is_finished_on_the_next_tick(
    tmp_path: Path,
) -> None:
    rig = InboxRig(tmp_path)
    rig.next_decision = decision_of(Label.MARKETING, reply=ReplyKind.ACKNOWLEDGEMENT)
    rig.receive(message_id="newsletter", sender="news@shop.example")
    rig.audit.failure = Killed()
    with pytest.raises(Killed):
        service_of(rig)[0].tick()
    rig.restart()
    rig.audit.failure = None
    service, _ = service_of(rig)

    result = service.tick()

    assert result.processed == 1
    assert [line.message_id for line in rig.audit.lines] == ["newsletter"]
    assert rig.mailbox.labels_of("newsletter") == {"MARKETING", PROCESSED}
    assert [message.kind for message in rig.mailbox.sent] == ["acknowledgement"]
    assert rig.processor.unfinished() == []


def test_a_transient_failure_of_the_final_modify_is_finished_on_the_next_tick(
    tmp_path: Path,
) -> None:
    rig = InboxRig(tmp_path)
    rig.next_decision = decision_of(Label.MARKETING, reply=ReplyKind.ACKNOWLEDGEMENT)
    rig.receive(message_id="newsletter", sender="news@shop.example")
    outage = SystemWideError("Gmail answered 503", next_action="wait")
    rig.mailbox.fail_next_modify("newsletter", changing=PROCESSED, error=outage)
    service, _ = service_of(rig)

    first, second = service.tick(), service.tick()

    assert (first.backoff_s, second.processed) == (30, 1)
    assert PROCESSED in rig.mailbox.labels_of("newsletter")
    assert [line.message_id for line in rig.audit.lines] == ["newsletter"]
    assert [message.kind for message in rig.mailbox.sent] == ["acknowledgement"]
    assert rig.processor.unfinished() == []


def waiting_approval(number: int) -> Approval:
    return Approval(
        id=f"a-{number}",
        source_message_id=f"source-{number}",
        source_thread_id=f"source-thread-{number}",
        attempt=0,
        actions=(),
        state=ApprovalState.AWAITING,
        request_thread_id=f"request-{number}",
        summary="s",
        holds=(),
    )
