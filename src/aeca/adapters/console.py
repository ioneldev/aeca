"""What AECA prints on stdout: JSON lines only, each one passed through the redactor."""

import json
from collections.abc import Callable

from aeca.adapters.json_log import summary
from aeca.adapters.redactor import Redactor
from aeca.application.ports import Clock
from aeca.domain.audit import AuditLine


class JsonConsole:
    def __init__(self, write_line: Callable[[str], None], redactor: Redactor, clock: Clock) -> None:
        self._write_line = write_line
        self._redactor = redactor
        self._clock = clock

    def email(self, line: AuditLine) -> None:
        self._write_line(self._redactor.text(summary(line)))

    def event(self, kind: str, **fields: object) -> None:
        record = self._redactor.value(
            {"ts": self._clock.now().isoformat(timespec="milliseconds"), "kind": kind, **fields}
        )
        self._write_line(json.dumps(record, ensure_ascii=True, allow_nan=False))
