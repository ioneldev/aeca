"""Incidents that only remember what was reported and cleared, for a test to read back."""

from aeca.application.incidents import IncidentKind


class RecordingIncidents:
    def __init__(self) -> None:
        self.reported: list[tuple[IncidentKind, str]] = []
        self.cleared: list[IncidentKind] = []
        # Raised by the next report(), as a send that failed would.
        self.report_failure: Exception | None = None

    def report(self, kind: IncidentKind, detail: str) -> None:
        if self.report_failure is not None:
            raise self.report_failure
        self.reported.append((kind, detail))

    def clear(self, kind: IncidentKind) -> None:
        self.cleared.append(kind)

    def kinds_reported(self) -> list[IncidentKind]:
        return [kind for kind, _detail in self.reported]
