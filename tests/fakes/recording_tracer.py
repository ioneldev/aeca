"""A tracer that keeps what it was told, for a test to read back."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RecordedEvent:
    kind: str
    fields: dict[str, object]


class RecordingTracer:
    def __init__(self) -> None:
        self.events: list[RecordedEvent] = []
        self.debug_lines: list[str] = []

    def event(self, kind: str, **fields: object) -> None:
        self.events.append(RecordedEvent(kind, fields))

    def debug(self, text: str) -> None:
        self.debug_lines.append(text)

    def of_kind(self, kind: str) -> list[RecordedEvent]:
        return [event for event in self.events if event.kind == kind]
