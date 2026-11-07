"""The parts of a Service a test about something else does not care about."""

from decimal import Decimal

from fakes.recording_incidents import RecordingIncidents
from fakes.recording_tracer import RecordingTracer

from aeca.application.watch import FailureWatch, WatchDeps

ALERT_AFTER_FAILURES = 3


class AlwaysAffordable:
    spent_usd = Decimal(0)

    def exhausted(self) -> bool:
        return False


def quiet_watch(incidents: RecordingIncidents | None = None) -> FailureWatch:
    return FailureWatch(
        WatchDeps(
            incidents=incidents or RecordingIncidents(),
            budget=AlwaysAffordable(),
            tracer=RecordingTracer(),
            alert_after_failures=ALERT_AFTER_FAILURES,
        )
    )


def nothing_to_start() -> None:
    return None
