"""What AECA prints on stdout: JSON lines only, each one passed through the redactor."""

import json
from collections.abc import Callable

from aeca.adapters.json_log import MAX_SUBJECT_CHARS, summary_fields
from aeca.adapters.redactor import Redactor
from aeca.application.ports import Clock
from aeca.domain.audit import AuditLine


class JsonConsole:
    def __init__(self, write_line: Callable[[str], None], redactor: Redactor, clock: Clock) -> None:
        self._write_line = write_line
        self._redactor = redactor
        self._clock = clock

    # Redacted before it is cut or serialised: a cut secret no longer matches, and JSON escaping
    # changes a secret that holds a quote or a backslash.
    def email(self, line: AuditLine) -> None:
        record = self._redactor.value(summary_fields(line))
        assert isinstance(record, dict)
        record["subject"] = str(record["subject"])[:MAX_SUBJECT_CHARS]
        self._write_line(
            json.dumps(record, ensure_ascii=True, allow_nan=False, separators=(",", ":"))
        )

    def event(self, kind: str, **fields: object) -> None:
        record = self._redactor.value(
            {"ts": self._clock.now().isoformat(timespec="milliseconds"), "kind": kind, **fields}
        )
        self._write_line(json.dumps(record, ensure_ascii=True, allow_nan=False))
