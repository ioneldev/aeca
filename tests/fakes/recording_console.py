"""A console that keeps what the service said, for a test to read back."""

from dataclasses import dataclass

from aeca.domain.audit import AuditLine


@dataclass(frozen=True, slots=True)
class ConsoleEvent:
    kind: str
    fields: dict[str, object]


class RecordingConsole:
    def __init__(self) -> None:
        self.emails: list[AuditLine] = []
        self.events: list[ConsoleEvent] = []

    def email(self, line: AuditLine) -> None:
        self.emails.append(line)

    def event(self, kind: str, **fields: object) -> None:
        self.events.append(ConsoleEvent(kind, fields))

    def kinds(self) -> list[str]:
        return [event.kind for event in self.events]
