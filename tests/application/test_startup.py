"""Startup checks every credential before anything is sent, and a failing start sends nothing:
Docker restarts a failing container, and Google suspends a new account that sends in bulk."""

import ssl
import threading
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

import pytest
from fakes.fake_calendar import FakeCalendar
from fakes.fake_mailbox import FakeMailbox
from fakes.recording_console import RecordingConsole
from fakes.recording_incidents import RecordingIncidents
from fakes.recording_tracer import RecordingTracer
from fakes.scripted_model import ScriptedModel
from google.auth.exceptions import RefreshError
from support.emails import make_raw
from support.quiet_service import NoCopyLock, NoHeartbeat, quiet_watch

from aeca.adapters.google_errors import classify
from aeca.adapters.sqlite_state import SqliteState
from aeca.application.executor import Executor, ExecutorDeps
from aeca.application.incidents import IncidentKind
from aeca.application.ports import ProviderError
from aeca.application.service import EmailProcessor, Service, ServiceDeps
from aeca.application.startup import (
    ExecutorActionResumer,
    ResumerDeps,
    StartupDeps,
    StartUpWork,
    resume_unfinished,
    start_up,
    startup_checks,
)
from aeca.domain.actions import Action, ActionStatus, Verb
from aeca.domain.approval import Approval, ApprovalState
from aeca.domain.errors import AecaError, ConfigError, SystemWideError
from aeca.domain.facts import FactSettings
from aeca.domain.journal import JournalEntry
from aeca.domain.labels import PROCESSED

ACCOUNT = "aeca@gmail.com"
MODEL = "claude-opus-5-5"
FACT_SETTINGS = FactSettings(
    account_address=ACCOUNT, internal_senders=frozenset(), bulk_senders=frozenset()
)
KEY_REFUSED = ProviderError(
    "Anthropic rejected the API key",
    next_action="Set ANTHROPIC_API_KEY to a valid key",
    kind="key_refused",
    system_wide=True,
)


class Rig:
    def __init__(self, state_dir: Path, *, internal_senders: frozenset[str] = frozenset()) -> None:
        self.mailbox = FakeMailbox(ACCOUNT)
        self.calendar = FakeCalendar()
        self.model = ScriptedModel()
        self.console = RecordingConsole()
        self.tracer = RecordingTracer()
        self.incidents = RecordingIncidents()
        self.store = SqliteState(state_dir / "state.db")
        self.checks = StartupDeps(
            mailbox=self.mailbox,
            calendar=self.calendar,
            model=self.model,
            model_name=MODEL,
            gmail_address=ACCOUNT,
            internal_senders=internal_senders or frozenset({"boss@example.com"}),
            console=self.console,
        )
        self.executor = Executor(
            ExecutorDeps(
                mailbox=self.mailbox,
                calendar=self.calendar,
                store=self.store,
                tracer=self.tracer,
                manager_address=ACCOUNT,
            )
        )
        self.resumer = ExecutorActionResumer(
            ResumerDeps(
                executor=self.executor,
                mailbox=self.mailbox,
                fact_settings=FACT_SETTINGS,
                tracer=self.tracer,
            )
        )

    def start_up(self, *, acquire_lock: Callable[[], None] = lambda: None) -> None:
        start_up(
            StartUpWork(
                checks=self.checks,
                resumer=self.resumer,
                store=self.store,
                incidents=self.incidents,
                acquire_lock=acquire_lock,
            )
        )

    def nothing_was_written_to_gmail(self) -> bool:
        return not (
            self.mailbox.sent
            or self.mailbox.drafts
            or self.mailbox.modify_log
            or self.mailbox.ensured_labels
        )

    def service(
        self, *, start: Callable[[], None], processor: EmailProcessor | None = None
    ) -> Service:
        return Service(
            ServiceDeps(
                processor=processor or _NothingToProcess(),
                mailbox=self.mailbox,
                console=self.console,
                poll_seconds=0.01,
                approvals=_NoApprovals(),
                watch=quiet_watch(),
                start_up=start,
                copy_lock=NoCopyLock(),
                heartbeat=NoHeartbeat(),
            )
        )


class _NothingToProcess:
    def process(self, message_id: str) -> None:
        raise AssertionError(f"nothing is polled before the start-up work is done: {message_id}")

    def unfinished(self) -> list[str]:
        return []


class _NoApprovals:
    def process_all(self) -> list[object]:
        return []


@pytest.fixture
def rig(tmp_path: Path) -> Rig:
    return Rig(tmp_path)


def test_a_passing_start_checks_each_credential_and_sends_nothing(rig: Rig) -> None:
    startup_checks(rig.checks)

    assert rig.model.access_checks == [MODEL]
    assert rig.nothing_was_written_to_gmail()


def test_the_address_check_ignores_case_and_gmail_dots(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    rig.mailbox = FakeMailbox("A.Eca@GMail.com")
    rig.checks = replace(rig.checks, mailbox=rig.mailbox)

    startup_checks(rig.checks)

    assert rig.model.access_checks == [MODEL]


def test_a_different_account_fails_with_its_next_action(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    rig.checks = replace(rig.checks, gmail_address="someone.else@gmail.com")

    with pytest.raises(ConfigError) as raised:
        startup_checks(rig.checks)

    message = str(raised.value)
    assert ACCOUNT in message and "someone.else@gmail.com" in message
    assert "GMAIL_ADDRESS" in raised.value.next_action
    assert "make auth" in raised.value.next_action


def test_a_revoked_token_names_make_auth(rig: Rig) -> None:
    rig.mailbox.address_failure = classify(RefreshError("invalid_grant: Token revoked"))

    with pytest.raises(AecaError) as raised:
        startup_checks(rig.checks)

    assert "make auth" in raised.value.next_action


def test_a_tls_failure_names_extra_ca_certs(rig: Rig) -> None:
    rig.calendar.zone_failure = classify(ssl.SSLError("certificate verify failed"))

    with pytest.raises(SystemWideError) as raised:
        startup_checks(rig.checks)

    assert "EXTRA_CA_CERTS" in raised.value.next_action
    assert "proxy" in str(raised.value)


def test_a_refused_key_fails_the_start_with_the_models_next_action(rig: Rig) -> None:
    rig.model.access_failure = KEY_REFUSED

    with pytest.raises(ProviderError) as raised:
        startup_checks(rig.checks)

    assert "ANTHROPIC_API_KEY" in raised.value.next_action


def test_an_empty_internal_senders_list_warns_once_on_the_console(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    rig.checks = replace(rig.checks, internal_senders=frozenset())

    startup_checks(rig.checks)

    assert rig.console.kinds() == ["warning"]
    assert rig.console.events[0].fields["setting"] == "INTERNAL_SENDERS"


def test_a_filled_internal_senders_list_does_not_warn(rig: Rig) -> None:
    startup_checks(rig.checks)

    assert rig.console.events == []


@pytest.mark.parametrize("failure", ["token", "tls", "other_account", "key"])
def test_a_credential_failure_across_ten_restarts_sends_no_mail(
    tmp_path: Path, failure: str
) -> None:
    """Spec §5 blocking case 6: every restart fails the same way, and no mail ever leaves."""
    mailboxes: list[FakeMailbox] = []
    for restart in range(10):
        (tmp_path / f"restart-{restart}").mkdir()
        rig = Rig(tmp_path / f"restart-{restart}")
        _break(rig, failure)
        mailboxes.append(rig.mailbox)

        with pytest.raises(AecaError):
            rig.service(start=rig.start_up).run(threading.Event())

        assert rig.nothing_was_written_to_gmail(), f"restart {restart} wrote to Gmail"
        assert rig.incidents.reported == [], "an incident was alerted from a failing start"

    assert all(not mailbox.sent and not mailbox.drafts for mailbox in mailboxes)


def test_a_failing_start_never_reaches_the_first_poll(rig: Rig) -> None:
    rig.mailbox.address_failure = classify(RefreshError("invalid_grant"))

    with pytest.raises(AecaError):
        rig.service(start=rig.start_up).run(threading.Event())

    assert rig.console.events == []


def test_a_passing_start_clears_a_refused_key_incident(rig: Rig) -> None:
    rig.start_up()

    assert rig.incidents.cleared == [IncidentKind.KEY_REFUSED]


def test_executing_actions_are_resolved_before_the_first_poll(rig: Rig) -> None:
    rig.mailbox.add(make_raw(message_id="m1", thread_id="t1", sender="client@example.com"))
    action = Action(Verb.APPLY_LABEL, key="k-label", args={"label": "TASK"})
    rig.store.journal_record(
        JournalEntry(source_message_id="m1", decision_json="{}", actions=(action,))
    )
    rig.store.action_update(action.key, ActionStatus.EXECUTING)
    polled_after_resume: list[str] = []

    stop = threading.Event()

    class StopAfterTheFirstPoll(_NothingToProcess):
        def unfinished(self) -> list[str]:
            polled_after_resume.append(_status(rig, action))
            stop.set()
            return []

    rig.service(start=rig.start_up, processor=StopAfterTheFirstPoll()).run(stop)

    assert polled_after_resume == [ActionStatus.DONE.value]
    assert _status(rig, action) == ActionStatus.DONE.value
    assert rig.mailbox.added_labels("m1") == ["TASK"]


def test_resume_unfinished_runs_every_pending_and_executing_action_once(rig: Rig) -> None:
    rig.mailbox.add(make_raw(message_id="m1", thread_id="t1", sender="client@example.com"))
    pending = Action(Verb.APPLY_LABEL, key="k-pending", args={"label": "TASK"})
    starred = Action(Verb.STAR, key="k-star", args={})
    rig.store.journal_record(
        JournalEntry(source_message_id="m1", decision_json="{}", actions=(pending, starred))
    )
    rig.store.action_update(starred.key, ActionStatus.EXECUTING)

    resumed = resume_unfinished(rig.resumer, rig.store)

    assert resumed == 2
    assert resume_unfinished(rig.resumer, rig.store) == 0
    assert PROCESSED not in rig.mailbox.labels_of("m1")


def test_an_action_whose_email_is_gone_is_traced_and_the_others_still_run(rig: Rig) -> None:
    rig.mailbox.add(make_raw(message_id="m2", thread_id="t2", sender="client@example.com"))
    lost = Action(Verb.STAR, key="k-lost", args={})
    kept = Action(Verb.STAR, key="k-kept", args={})
    rig.store.journal_record(
        JournalEntry(source_message_id="gone", decision_json="{}", actions=(lost,))
    )
    rig.store.journal_record(
        JournalEntry(source_message_id="m2", decision_json="{}", actions=(kept,))
    )

    resumed = resume_unfinished(rig.resumer, rig.store)

    assert resumed == 1
    assert [event.kind for event in rig.tracer.events] == ["resume_failed"]
    assert _status(rig, kept) == ActionStatus.DONE.value


def _break(rig: Rig, failure: str) -> None:
    if failure == "token":
        rig.mailbox.address_failure = classify(RefreshError("invalid_grant"))
    elif failure == "tls":
        rig.calendar.zone_failure = classify(ssl.SSLError("certificate verify failed"))
    elif failure == "other_account":
        rig.checks = replace(rig.checks, gmail_address="other@gmail.com")
    else:
        rig.model.access_failure = KEY_REFUSED


def _status(rig: Rig, action: Action) -> str:
    record = rig.store.action_get(action.key)
    assert record is not None
    return record.status.value


def test_resume_unfinished_skips_the_actions_of_an_approval_that_is_not_approved(rig: Rig) -> None:
    rig.mailbox.add(make_raw(message_id="m1", thread_id="t1", sender="client@example.com"))
    gated = Action(Verb.STAR, key="k-gated", args={})
    rig.store.approval_save(
        Approval(
            id="a-1",
            source_message_id="m1",
            source_thread_id="t1",
            attempt=0,
            actions=(gated,),
            state=ApprovalState.AWAITING,
            request_thread_id=None,
            summary="s",
            holds=(),
        )
    )

    assert resume_unfinished(rig.resumer, rig.store) == 0
    assert _status(rig, gated) == ActionStatus.PENDING.value
    assert rig.mailbox.modify_log == []


def test_a_failing_check_never_touches_the_lock(rig: Rig) -> None:
    rig.model.access_failure = KEY_REFUSED
    taken: list[str] = []

    with pytest.raises(ProviderError):
        rig.start_up(acquire_lock=lambda: taken.append("lock"))

    assert taken == []


def test_a_refused_lock_stops_the_start_before_anything_is_written(rig: Rig) -> None:
    def refused() -> None:
        raise ConfigError("Another copy holds the lock.", next_action="Stop the other copy")

    with pytest.raises(ConfigError, match="Another copy"):
        rig.start_up(acquire_lock=refused)

    assert rig.nothing_was_written_to_gmail()
