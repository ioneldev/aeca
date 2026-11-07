"""Writes audit.jsonl: one redacted, bounded line per processed email."""

import json
from dataclasses import asdict
from pathlib import Path

from aeca.adapters.redactor import Redactor
from aeca.adapters.rotating_log import RotatingLogFile, Rotation
from aeca.domain.audit import AuditLine
from aeca.domain.secret import SecretStr

MAX_FIELD_CHARS = 200
_BOUNDED_FIELDS = ("sender", "subject")


class JsonlAuditLog:
    def __init__(self, path: Path, *, secrets: tuple[SecretStr, ...], rotation: Rotation) -> None:
        self._redactor = Redactor(secrets)
        self._file = RotatingLogFile(path, rotation)

    def write(self, line: AuditLine) -> None:
        record = self._redactor.value(asdict(line))
        assert isinstance(record, dict)
        for field in _BOUNDED_FIELDS:
            record[field] = _bounded(record[field])
        # ensure_ascii, because a subject is attacker-controlled and U+2028 or U+0085 would split
        # one audit line in two for any reader that uses splitlines().
        self._file.append_line(json.dumps(record, ensure_ascii=True, allow_nan=False))


# Cut after redaction, because a secret cut in half no longer matches and its head would leak.
def _bounded(value: object) -> str:
    return str(value)[:MAX_FIELD_CHARS]
