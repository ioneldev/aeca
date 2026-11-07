"""One alert per incident, not one per email, and a restart does not alert again."""

from datetime import timedelta
from pathlib import Path

import pytest
from fakes.fake_mailbox import FakeMailbox
from fakes.fixed_clock import FIXED_NOW, FixedClock

from aeca.adapters.sqlite_state import SqliteState
from aeca.application.incidents import IncidentKind, Incidents, IncidentsDeps
from aeca.domain.errors import SystemWideError
from aeca.domain.labels import PROCESSED

ACCOUNT = "aeca@gmail.com"
MANAGER = "boss@example.com"


class Rig:
    def __init__(self, state_dir: Path) -> None:
        self.state_dir = state_dir
        state_dir.mkdir(parents=True, exist_ok=True)
        self.mailbox = FakeMailbox(ACCOUNT)
        self.clock = FixedClock()
        self.volumes = 0
        self.incidents = self._on_a_new_volume()

    def restart(self) -> None:
        self.incidents = self._incidents_on(self.state_dir / f"state-{self.volumes}.db")

    def restart_with_the_volume_wiped(self) -> None:
        self.incidents = self._on_a_new_volume()

    def _on_a_new_volume(self) -> Incidents:
        self.volumes += 1
        return self._incidents_on(self.state_dir / f"state-{self.volumes}.db")

    def _incidents_on(self, path: Path) -> Incidents:
        return Incidents(
            IncidentsDeps(
                store=SqliteState(path),
                mailbox=self.mailbox,
                clock=self.clock,
                manager_address=MANAGER,
            )
        )


@pytest.fixture
def rig(tmp_path: Path) -> Rig:
    return Rig(tmp_path)


def test_an_incident_sends_one_alert_to_the_manager(rig: Rig) -> None:
    rig.incidents.report(IncidentKind.FAILURES_IN_A_ROW, "3 emails failed")

    assert len(rig.mailbox.sent) == 1
    alert = rig.mailbox.sent[0]
    assert (alert.to, alert.kind) == (MANAGER, "alert")
    assert "3 emails failed" in alert.body
    assert alert.aeca_id


def test_the_same_incident_reported_again_sends_nothing(rig: Rig) -> None:
    for _ in range(5):
        rig.incidents.report(IncidentKind.COST_CAP, "spent")

    assert len(rig.mailbox.sent) == 1


def test_an_incident_alerts_once_across_a_restart(rig: Rig) -> None:
    rig.incidents.report(IncidentKind.KEY_REFUSED, "refused")

    rig.restart()
    rig.incidents.report(IncidentKind.KEY_REFUSED, "refused")

    assert len(rig.mailbox.sent) == 1


def test_a_wiped_volume_does_not_alert_again_on_the_same_day(rig: Rig) -> None:
    rig.incidents.report(IncidentKind.KEY_REFUSED, "refused")

    rig.restart_with_the_volume_wiped()
    rig.incidents.report(IncidentKind.KEY_REFUSED, "refused")

    assert len(rig.mailbox.sent) == 1


def test_different_incidents_each_alert(rig: Rig) -> None:
    rig.incidents.report(IncidentKind.KEY_REFUSED, "refused")
    rig.incidents.report(IncidentKind.COST_CAP, "spent")

    assert len(rig.mailbox.sent) == 2
    assert len({alert.aeca_id for alert in rig.mailbox.sent}) == 2


def test_a_second_incident_of_the_same_kind_on_the_same_day_sends_no_second_alert(
    rig: Rig,
) -> None:
    """Deliberate: the alert's id derives from the kind and the UTC date, so a restart with a
    wiped volume cannot repeat it, and a flapping fault cannot send mail in bulk."""
    rig.incidents.report(IncidentKind.FAILURES_IN_A_ROW, "3 emails failed")
    rig.incidents.clear(IncidentKind.FAILURES_IN_A_ROW)

    rig.incidents.report(IncidentKind.FAILURES_IN_A_ROW, "3 emails failed again")

    assert len(rig.mailbox.sent) == 1


def test_a_new_incident_on_a_later_day_alerts_again(rig: Rig) -> None:
    rig.incidents.report(IncidentKind.COST_CAP, "spent")
    rig.incidents.clear(IncidentKind.COST_CAP)
    rig.clock = FixedClock(FIXED_NOW + timedelta(days=1))
    rig.restart()

    rig.incidents.report(IncidentKind.COST_CAP, "spent")

    assert len(rig.mailbox.sent) == 2


def test_a_failed_send_leaves_the_incident_unreported_so_the_next_try_alerts(rig: Rig) -> None:
    rig.mailbox.send_failures.append(SystemWideError("down", next_action="wait"))

    with pytest.raises(SystemWideError):
        rig.incidents.report(IncidentKind.COST_CAP, "spent")
    rig.incidents.report(IncidentKind.COST_CAP, "spent")

    assert len(rig.mailbox.sent) == 1


def test_the_alert_is_labelled_processed_so_the_inbox_query_never_returns_it(rig: Rig) -> None:
    rig.incidents.report(IncidentKind.COST_CAP, "spent")

    assert [call.add for call in rig.mailbox.modify_log] == [(PROCESSED,)]


@pytest.mark.parametrize("kind", list(IncidentKind))
def test_every_alert_says_what_happened_and_what_to_do_and_never_starts_with_an_answer_word(
    rig: Rig, kind: IncidentKind
) -> None:
    rig.incidents.report(kind, "the detail")

    body = rig.mailbox.sent[0].body
    assert "the detail" in body
    assert "AECA" in rig.mailbox.sent[0].subject
    for line in body.splitlines():
        assert not line.strip().upper().startswith(("APPROVE", "REJECT"))
