"""The executor runs each recorded action once: a label change is one modify of one message, and a
send goes through a recorded draft, so neither a crash nor a lost state file sends it twice."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fakes.crash import Killed
from fakes.fake_calendar import FakeCalendar
from fakes.fake_mailbox import FakeMailbox
from fakes.recording_tracer import RecordingTracer
from support.emails import make_raw

from aeca.adapters.sqlite_state import SqliteState
from aeca.application.executor import (
    RECENT_SENT_SEARCHED,
    ActionFailed,
    Executor,
    ExecutorDeps,
)
from aeca.domain.actions import Action, ActionStatus, Verb
from aeca.domain.approval import Approval, ApprovalState
from aeca.domain.errors import CredentialsRevoked, EmailError
from aeca.domain.facts import EmailFacts, FactSettings, extract_facts
from aeca.domain.journal import JournalEntry
from aeca.domain.labels import PROCESSED

ACCOUNT = "aeca@gmail.com"
MANAGER = "boss@example.com"
SENDER = "client@example.com"
SENT_AT = datetime(2026, 11, 7, 10, 0, tzinfo=UTC)


class Rig:
    def __init__(self, state_dir: Path) -> None:
        self.mailbox = FakeMailbox(ACCOUNT)
        self.store = SqliteState(state_dir / "state.db")
        self.tracer = RecordingTracer()
        raw = make_raw(sender=SENDER, subject="Question", message_id="m1", thread_id="t1")
        self.mailbox.add(raw)
        self.source: EmailFacts = extract_facts(
            raw,
            FactSettings(
                account_address=ACCOUNT, internal_senders=frozenset(), bulk_senders=frozenset()
            ),
        )
        self.executor = Executor(
            ExecutorDeps(
                mailbox=self.mailbox,
                calendar=FakeCalendar(),
                store=self.store,
                tracer=self.tracer,
                manager_address=MANAGER,
            )
        )

    def record(self, *actions: Action) -> tuple[Action, ...]:
        self.store.journal_record(
            JournalEntry(source_message_id="m1", decision_json="{}", actions=actions)
        )
        return actions

    def status_of(self, action: Action) -> ActionStatus:
        record = self.store.action_get(action.key)
        assert record is not None
        return record.status

    def ref_of(self, action: Action) -> str | None:
        record = self.store.action_get(action.key)
        assert record is not None
        return record.ref


@pytest.fixture
def rig(tmp_path: Path) -> Rig:
    return Rig(tmp_path)


def label(name: str = "TASK", key: str = "k-label") -> Action:
    return Action(verb=Verb.APPLY_LABEL, key=key, args={"label": name})


def plain(verb: Verb, key: str) -> Action:
    return Action(verb=verb, key=key, args={})


def acknowledgement(key: str = "k-ack") -> Action:
    return Action(verb=Verb.SEND_REPLY, key=key, args={"template": "acknowledgement"})


def notice(key: str = "k-notice") -> Action:
    return Action(
        verb=Verb.NOTIFY_MANAGER, key=key, args={"summary": "Needs the numbers.", "quote": "today"}
    )


def confirmation(key: str = "k-confirm") -> Action:
    return Action(
        verb=Verb.SEND_REPLY,
        key=key,
        args={
            "template": "confirmation",
            "start": "2026-11-10T15:00:00+02:00",
            "end": "2026-11-10T15:30:00+02:00",
            "zone": "Europe/Bucharest",
        },
    )


@pytest.mark.parametrize(
    ("action", "add", "remove"),
    [
        (label("TASK"), ("TASK",), ()),
        (plain(Verb.STAR, "k-star"), ("STARRED",), ()),
        (plain(Verb.ARCHIVE, "k-archive"), (), ("INBOX",)),
        (plain(Verb.MOVE_TO_SPAM, "k-spam"), ("SPAM",), ()),
    ],
)
def test_each_label_verb_is_one_modify_of_the_source_message(
    rig: Rig, action: Action, add: tuple[str, ...], remove: tuple[str, ...]
) -> None:
    rig.record(action)

    rig.executor.run([action], rig.source)

    changes = [(call.message_id, call.add, call.remove) for call in rig.mailbox.modify_log]
    assert changes == [("m1", add, remove)]
    assert rig.status_of(action) is ActionStatus.DONE


def test_an_action_already_done_is_not_run_again(rig: Rig) -> None:
    action = label()
    rig.record(action)
    rig.executor.run([action], rig.source)

    rig.executor.run([action], rig.source)

    assert len(rig.mailbox.modify_log) == 1


def test_an_action_that_was_never_recorded_is_refused_before_any_side_effect(rig: Rig) -> None:
    recorded = rig.record(label())[0]
    unrecorded = plain(Verb.ARCHIVE, "k-never-recorded")

    with pytest.raises(EmailError, match="k-never-recorded"):
        rig.executor.run([recorded, unrecorded], rig.source)

    assert rig.mailbox.modify_log == []


def test_it_stops_at_the_first_failed_action_and_later_ones_stay_pending(rig: Rig) -> None:
    first, second, third = rig.record(label(), acknowledgement(), plain(Verb.ARCHIVE, "k-arch"))
    rig.mailbox.send_failures.append(EmailError("Gmail refused the send", next_action="none"))

    with pytest.raises(ActionFailed) as failure:
        rig.executor.run([first, second, third], rig.source)

    assert [r.status for r in failure.value.records] == [
        ActionStatus.DONE, ActionStatus.FAILED, ActionStatus.PENDING,
    ]  # fmt: skip
    assert failure.value.action == second
    assert "Gmail refused the send" in str(failure.value)
    assert rig.status_of(third) is ActionStatus.PENDING
    assert rig.mailbox.labels_of("m1") >= {"INBOX"}
    assert rig.tracer.of_kind("action_failed")[0].fields["key"] == second.key


def test_a_system_wide_error_leaves_the_action_as_it_was_and_is_raised(rig: Rig) -> None:
    action = rig.record(label())[0]
    rig.mailbox.modify_failures.append(CredentialsRevoked("revoked", next_action="none"))

    with pytest.raises(CredentialsRevoked):
        rig.executor.run([action], rig.source)

    assert rig.status_of(action) is ActionStatus.PENDING


def test_a_send_records_its_draft_then_sends_then_records_the_sent_message(rig: Rig) -> None:
    action = rig.record(acknowledgement())[0]
    rig.mailbox.send_failures.append(Killed())

    with pytest.raises(Killed):
        rig.executor.run([action], rig.source)
    [draft_id] = rig.mailbox.drafts
    in_flight = (rig.status_of(action), rig.ref_of(action))
    rig.executor.run([action], rig.source)

    assert in_flight == (ActionStatus.EXECUTING, draft_id)
    [sent] = rig.mailbox.thread_summaries("t1")[1:]
    assert (rig.status_of(action), rig.ref_of(action)) == (ActionStatus.DONE, sent.message_id)
    assert PROCESSED in rig.mailbox.labels_of(sent.message_id)


def test_a_reply_goes_to_the_original_sender_in_its_thread_with_the_action_key(rig: Rig) -> None:
    action = rig.record(acknowledgement())[0]

    rig.executor.run([action], rig.source)

    [reply] = rig.mailbox.sent
    assert (reply.to, reply.thread_id, reply.aeca_id, reply.kind) == (
        SENDER, "t1", action.key, "acknowledgement",
    )  # fmt: skip


def test_a_reply_already_in_the_thread_with_its_key_is_not_sent_again(rig: Rig) -> None:
    action = rig.record(confirmation())[0]
    rig.mailbox.add(
        make_raw(
            sender=ACCOUNT,
            headers={"X-AECA-Kind": "confirmation", "X-AECA-Id": action.key},
            label_ids=("SENT",),
            message_id="sent-earlier",
            thread_id="t1",
        )
    )

    rig.executor.run([action], rig.source)

    assert rig.mailbox.sent == [] and rig.mailbox.drafts == {}
    assert (rig.status_of(action), rig.ref_of(action)) == (ActionStatus.DONE, "sent-earlier")


def test_a_message_with_the_key_that_aeca_did_not_send_does_not_count(rig: Rig) -> None:
    action = rig.record(acknowledgement())[0]
    rig.mailbox.add(
        make_raw(
            sender=SENDER,
            headers={"X-AECA-Kind": "acknowledgement", "X-AECA-Id": action.key},
            message_id="forged",
            thread_id="t1",
        )
    )

    rig.executor.run([action], rig.source)

    assert [message.kind for message in rig.mailbox.sent] == ["acknowledgement"]


def test_a_recorded_draft_that_still_exists_is_sent_and_no_new_draft_is_made(rig: Rig) -> None:
    action = rig.record(notice())[0]
    rig.mailbox.send_failures.append(Killed())
    with pytest.raises(Killed):
        rig.executor.run([action], rig.source)

    rig.executor.run([action], rig.source)

    assert [message.kind for message in rig.mailbox.sent] == ["urgent-notice"]
    assert rig.mailbox.drafts == {}


def test_a_recorded_draft_that_is_gone_counts_as_sent(rig: Rig) -> None:
    action = rig.record(notice())[0]
    rig.mailbox.kill_after_entry = "send:urgent-notice"
    with pytest.raises(Killed):
        rig.executor.run([action], rig.source)

    rig.executor.run([action], rig.source)

    assert [message.kind for message in rig.mailbox.sent] == ["urgent-notice"]
    assert rig.status_of(action) is ActionStatus.DONE


def test_a_failed_send_keeps_its_draft_and_the_retry_sends_that_draft(rig: Rig) -> None:
    action = rig.record(acknowledgement())[0]
    rig.mailbox.send_failures.append(EmailError("Gmail refused the send", next_action="none"))
    with pytest.raises(ActionFailed):
        rig.executor.run([action], rig.source)
    [draft_id] = rig.mailbox.drafts

    rig.executor.run([action], rig.source)

    assert rig.status_of(action) is ActionStatus.DONE
    assert [message.kind for message in rig.mailbox.sent] == ["acknowledgement"]
    assert draft_id not in rig.mailbox.drafts


def test_the_urgent_notice_goes_to_the_manager_in_a_thread_of_its_own(rig: Rig) -> None:
    action = rig.record(notice())[0]

    rig.executor.run([action], rig.source)

    [sent] = rig.mailbox.sent
    assert (sent.to, sent.thread_id, sent.in_reply_to, sent.references) == (
        MANAGER, None, None, None,
    )  # fmt: skip
    assert (sent.kind, sent.aeca_id) == ("urgent-notice", action.key)
    assert "Needs the numbers." in sent.body and "today" in sent.body


def test_run_returns_each_actions_record_in_order(rig: Rig) -> None:
    actions = rig.record(label(), plain(Verb.STAR, "k-star"))

    records = rig.executor.run(actions, rig.source)

    assert [(r.action, r.status) for r in records] == [
        (actions[0], ActionStatus.DONE), (actions[1], ActionStatus.DONE),
    ]  # fmt: skip


# F1: a message in a thread of its own has no thread to check, so the recent sent mail is checked.


def sent_by_aeca(message_id: str, aeca_id: str, *, minutes_later: int = 0) -> object:
    return make_raw(
        sender=ACCOUNT,
        to=MANAGER,
        headers={"X-AECA-Kind": "urgent-notice", "X-AECA-Id": aeca_id},
        label_ids=("SENT",),
        message_id=message_id,
        thread_id=f"thread-of-{message_id}",
        internal_date=SENT_AT + timedelta(minutes=minutes_later),
    )


def test_a_notice_among_the_recent_sent_mail_is_not_sent_again(rig: Rig) -> None:
    action = rig.record(notice())[0]
    rig.mailbox.add(sent_by_aeca("sent-before-the-wipe", action.key))  # type: ignore[arg-type]

    rig.executor.run([action], rig.source)

    assert rig.mailbox.sent == [] and rig.mailbox.drafts == {}
    assert (rig.status_of(action), rig.ref_of(action)) == (
        ActionStatus.DONE, "sent-before-the-wipe",
    )  # fmt: skip
    assert rig.mailbox.recent_sent_reads == [RECENT_SENT_SEARCHED] == [25]


def test_a_notice_sent_further_back_than_the_recent_window_is_sent_again(rig: Rig) -> None:
    action = rig.record(notice())[0]
    rig.mailbox.add(sent_by_aeca("long-ago", action.key))  # type: ignore[arg-type]
    for number in range(30):
        later = sent_by_aeca(f"later-{number}", f"k-other-{number}", minutes_later=number + 1)
        rig.mailbox.add(later)  # type: ignore[arg-type]

    rig.executor.run([action], rig.source)

    assert [message.kind for message in rig.mailbox.sent] == ["urgent-notice"]


def test_a_reply_checks_its_thread_and_not_the_recent_sent_mail(rig: Rig) -> None:
    action = rig.record(acknowledgement())[0]

    rig.executor.run([action], rig.source)

    assert rig.mailbox.recent_sent_reads == []
    assert rig.mailbox.thread_reads == ["t1"]


# F4: an approved reply carries a kind of its own, so it never counts as the unapproved one.


def test_an_approved_decline_is_sent_with_a_kind_of_its_own(rig: Rig) -> None:
    decline = Action(verb=Verb.SEND_REPLY, key="k-decline@0", args={"template": "decline"})
    rig.store.approval_save(
        Approval(
            id="a-1",
            source_message_id="m1",
            source_thread_id="t1",
            attempt=0,
            actions=(decline,),
            state=ApprovalState.APPROVED,
            request_thread_id=None,
            summary="s",
            holds=(),
        )
    )

    rig.executor.run([decline], rig.source)

    assert [message.kind for message in rig.mailbox.sent] == ["approved-decline"]


def test_an_automatic_decline_keeps_its_kind(rig: Rig) -> None:
    decline = rig.record(Action(verb=Verb.SEND_REPLY, key="k-d", args={"template": "decline"}))[0]

    rig.executor.run([decline], rig.source)

    assert [message.kind for message in rig.mailbox.sent] == ["decline"]
