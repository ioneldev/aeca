"""The service says when it is broken: a refused key, the cost cap and emails failing in a row each
send one alert per incident, and nothing it says depends on Gmail being up for the tick."""

from dataclasses import replace
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from fakes.fake_mailbox import FakeMailbox
from fakes.fixed_clock import FIXED_NOW
from fakes.recording_console import RecordingConsole
from fakes.recording_incidents import RecordingIncidents
from fakes.recording_tracer import RecordingTracer
from fakes.scripted_model import ScriptedModel
from support.audit_lines import line_for
from support.facts import make_facts
from support.quiet_service import nothing_to_start

from aeca.adapters.sqlite_state import SqliteState
from aeca.application.budget import Budget, StoreLedger
from aeca.application.decide import DecideDeps, decide
from aeca.application.incidents import IncidentKind, Incidents, IncidentsDeps
from aeca.application.ports import ProviderError
from aeca.application.service import Service, ServiceDeps
from aeca.application.watch import FailureWatch, WatchDeps
from aeca.domain.audit import AuditLine
from aeca.domain.decision import ModelDecision
from aeca.domain.errors import EmailError, SystemWideError

ACCOUNT = "aeca@gmail.com"
MANAGER = "boss@example.com"
ALERT_AFTER = 3
DAILY_LIMIT = Decimal("0.02")


class MovableClock:
    def __init__(self) -> None:
        self.current = FIXED_NOW

    def now(self) -> datetime:
        return self.current

    def next_day(self) -> None:
        self.current += timedelta(days=1)


class Processor:
    """Each email id maps to a line, an exception, or the real decision, which costs money."""

    def __init__(self, decide_with: DecideDeps) -> None:
        self.script: dict[str, AuditLine | Exception] = {}
        self._decide_with = decide_with

    def unfinished(self) -> list[str]:
        return []

    def process(self, message_id: str) -> AuditLine | None:
        scripted = self.script.get(message_id)
        if isinstance(scripted, Exception):
            raise scripted
        if scripted is None:
            decide(make_facts(message_id=message_id), self._decide_with)
            return line_for(message_id)
        return scripted


class _ApprovalsNone:
    def process_all(self) -> list[AuditLine]:
        return []


class Rig:
    def __init__(self, state_dir: Path, *, fail_alerts: bool = False) -> None:
        self.clock = MovableClock()
        self.store = SqliteState(state_dir / "state.db")
        self.mailbox = FakeMailbox(ACCOUNT)
        self.console = RecordingConsole()
        self.tracer = RecordingTracer()
        self.model = ScriptedModel()
        self.budget = Budget(DAILY_LIMIT, ledger=StoreLedger(self.store, self.clock))
        self.processor = Processor(
            DecideDeps(
                model=self.model,
                model_name="claude-opus-5-5",
                effort="low",
                clock=self.clock,
                zone=ZoneInfo("Europe/Bucharest"),
                tracer=self.tracer,
                budget=self.budget,
            )
        )
        self.incidents = RecordingIncidents() if fail_alerts else self._real_incidents()
        self.service = Service(
            ServiceDeps(
                processor=self.processor,
                mailbox=self.mailbox,
                console=self.console,
                poll_seconds=0.01,
                approvals=_ApprovalsNone(),
                watch=FailureWatch(
                    WatchDeps(
                        incidents=self.incidents,
                        budget=self.budget,
                        tracer=self.tracer,
                        alert_after_failures=ALERT_AFTER,
                    )
                ),
                start_up=nothing_to_start,
            )
        )

    def _real_incidents(self) -> Incidents:
        return Incidents(
            IncidentsDeps(
                store=self.store,
                mailbox=self.mailbox,
                clock=self.clock,
                manager_address=MANAGER,
            )
        )

    def inbox(self, *ids: str) -> None:
        self.mailbox.listing_override = list(ids)

    def failing(self, *ids: str) -> None:
        for message_id in ids:
            self.processor.script[message_id] = line_for(message_id, outcome="failed")

    def alerts_sent(self) -> list[str]:
        return [message.subject for message in self.mailbox.sent if message.kind == "alert"]

    def incident_is_open(self, kind: IncidentKind) -> bool:
        """Opening an open incident reports False, and opening a closed one leaves it open."""
        opened_now = self.store.incident_open(kind.value)
        if opened_now:
            self.store.incident_close(kind.value)
        return not opened_now


@pytest.fixture
def rig(tmp_path: Path) -> Rig:
    return Rig(tmp_path)


def test_three_failures_in_a_row_alert_once(rig: Rig) -> None:
    rig.failing("a", "b", "c", "d")
    rig.inbox("a", "b", "c", "d")

    rig.service.tick()
    rig.service.tick()

    assert len(rig.alerts_sent()) == 1
    assert rig.incident_is_open(IncidentKind.FAILURES_IN_A_ROW)


def test_fewer_failures_than_the_threshold_send_nothing(rig: Rig) -> None:
    rig.failing("a", "b")
    rig.inbox("a", "b")

    rig.service.tick()

    assert rig.alerts_sent() == []


def test_a_good_email_resets_the_count(rig: Rig) -> None:
    rig.processor.script["good"] = line_for("good")
    rig.failing("a", "b", "d", "e")
    rig.inbox("a", "b", "good", "d", "e")

    rig.service.tick()

    assert rig.alerts_sent() == []


def test_a_good_email_closes_the_incident(rig: Rig) -> None:
    rig.failing("a", "b", "c")
    rig.inbox("a", "b", "c")
    rig.service.tick()

    rig.processor.script["good"] = line_for("good")
    rig.inbox("good")
    rig.service.tick()

    assert not rig.incident_is_open(IncidentKind.FAILURES_IN_A_ROW)


def test_one_email_retried_on_every_poll_is_one_failure_not_three(rig: Rig) -> None:
    rig.failing("stuck")
    rig.inbox("stuck")

    for _ in range(5):
        rig.service.tick()

    assert rig.alerts_sent() == []


def test_an_email_error_that_escapes_the_processor_counts_as_a_failure(rig: Rig) -> None:
    for message_id in ("a", "b", "c"):
        rig.processor.script[message_id] = EmailError("unreadable", next_action="none")
    rig.inbox("a", "b", "c")

    rig.service.tick()

    assert len(rig.alerts_sent()) == 1


def test_the_failure_alert_names_the_count_and_the_latest_error(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    for message_id in ("a", "b", "c"):
        rig.processor.script[message_id] = EmailError(f"{message_id} unreadable", next_action="x")
    rig.inbox("a", "b", "c")

    rig.service.tick()

    body = rig.mailbox.sent[0].body
    assert "3" in body and "c unreadable" in body


@pytest.mark.parametrize("kind", ["key_refused", "authentication", "permission"])
def test_a_refused_key_alerts(rig: Rig, kind: str) -> None:
    refused = ProviderError("refused", next_action="set the key", kind=kind, system_wide=True)
    rig.processor.script["a"] = refused
    rig.inbox("a")

    result = rig.service.tick()
    rig.service.tick()

    assert result.backoff_s > 0
    assert len(rig.alerts_sent()) == 1
    assert "refused" in rig.mailbox.sent[0].body


def test_an_outage_that_is_not_the_key_sends_no_alert(rig: Rig) -> None:
    rig.processor.script["a"] = ProviderError(
        "busy", next_action="wait", kind="rate_limit", system_wide=True
    )
    rig.processor.script["b"] = SystemWideError("down", next_action="wait")
    rig.inbox("a")
    rig.service.tick()
    rig.inbox("b")
    rig.service.tick()

    assert rig.alerts_sent() == []


def test_the_cost_cap_stops_model_calls_and_sends_one_notice(rig: Rig) -> None:
    rig.model.script(*[_a_decision() for _ in range(2)])
    rig.inbox("m1", "m2", "m3")

    for _ in range(3):
        rig.service.tick()

    assert rig.model.calls == 2
    assert len(rig.alerts_sent()) == 1
    assert "email_failed" not in rig.console.kinds()
    assert "backoff" in rig.console.kinds()


def test_the_cost_cap_closes_when_a_new_utc_day_allows_spending(rig: Rig) -> None:
    rig.model.script(*[_a_decision() for _ in range(2)])
    rig.inbox("m1", "m2", "m3")
    rig.service.tick()
    assert rig.incident_is_open(IncidentKind.COST_CAP)

    rig.clock.next_day()
    rig.inbox()
    rig.service.tick()

    assert not rig.incident_is_open(IncidentKind.COST_CAP)


def test_the_cost_cap_stays_open_while_the_day_is_still_spent(rig: Rig) -> None:
    rig.model.script(*[_a_decision() for _ in range(2)])
    rig.inbox("m1", "m2", "m3")
    rig.service.tick()

    rig.inbox()
    rig.service.tick()

    assert rig.incident_is_open(IncidentKind.COST_CAP)


def test_a_failing_alert_does_not_fail_the_tick_and_is_traced(tmp_path: Path) -> None:
    rig = Rig(tmp_path, fail_alerts=True)
    assert isinstance(rig.incidents, RecordingIncidents)
    rig.incidents.report_failure = SystemWideError("gmail down", next_action="wait")
    rig.failing("a", "b", "c")
    rig.inbox("a", "b", "c")

    result = rig.service.tick()

    assert result.failed == 3
    assert [event.kind for event in rig.tracer.events if event.kind == "alert_failed"] == [
        "alert_failed"
    ]


def _a_decision() -> ModelDecision:
    return ModelDecision(primary_label="TASK", summary="Asks for the Q3 numbers.")


def test_a_key_refusal_closes_once_a_paid_email_succeeds_and_a_later_refusal_alerts_again(
    tmp_path: Path,
) -> None:
    rig = Rig(tmp_path, fail_alerts=True)
    assert isinstance(rig.incidents, RecordingIncidents)
    refused = ProviderError("refused", next_action="k", kind="key_refused", system_wide=True)
    rig.processor.script["a"] = refused
    rig.inbox("a")
    rig.service.tick()

    rig.processor.script["paid"] = replace(line_for("paid"), usd=Decimal("0.01"))
    rig.inbox("paid")
    rig.service.tick()
    rig.processor.script["b"] = refused
    rig.inbox("b")
    rig.service.tick()

    assert rig.incidents.cleared.count(IncidentKind.KEY_REFUSED) == 1
    assert rig.incidents.kinds_reported() == [IncidentKind.KEY_REFUSED] * 2


def test_an_email_that_cost_nothing_does_not_prove_the_key_works(tmp_path: Path) -> None:
    rig = Rig(tmp_path, fail_alerts=True)
    assert isinstance(rig.incidents, RecordingIncidents)
    rig.processor.script["rule"] = line_for("rule")
    rig.inbox("rule")

    rig.service.tick()

    assert IncidentKind.KEY_REFUSED not in rig.incidents.cleared


def test_a_call_that_overshoots_the_cap_alerts_when_the_cap_is_hit(rig: Rig) -> None:
    rig.model.script(*[_a_decision() for _ in range(2)])
    rig.inbox("m1", "m2")

    rig.service.tick()

    assert rig.model.calls == 2
    assert len(rig.alerts_sent()) == 1

    rig.inbox()
    rig.service.tick()
    assert len(rig.alerts_sent()) == 1


def test_a_paid_email_before_a_refusal_in_the_same_tick_does_not_close_the_incident(
    tmp_path: Path,
) -> None:
    rig = Rig(tmp_path, fail_alerts=True)
    assert isinstance(rig.incidents, RecordingIncidents)
    rig.processor.script["paid"] = replace(line_for("paid"), usd=Decimal("0.01"))
    rig.processor.script["refused"] = ProviderError(
        "refused", next_action="k", kind="key_refused", system_wide=True
    )
    rig.inbox("paid", "refused")
    rig.service.tick()

    rig.inbox()
    rig.service.tick()

    assert IncidentKind.KEY_REFUSED not in rig.incidents.cleared
