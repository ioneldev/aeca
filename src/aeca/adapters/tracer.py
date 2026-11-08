"""Writes trace.jsonl, and debug-YYYY-MM-DD.log when enabled, with every secret replaced by a
marker."""

import json
import re
from datetime import date, timedelta
from pathlib import Path

from aeca.adapters.redactor import REDACTED, Redactor
from aeca.adapters.rotating_log import RotatingLogFile, Rotation
from aeca.application.ports import Clock
from aeca.domain.secret import SecretStr

__all__ = ["REDACTED", "JsonlTracer"]

DEFAULT_DEBUG_DAYS = 7
DEBUG_FILE_PATTERN = re.compile(r"^debug-(\d{4}-\d{2}-\d{2})\.log(\.\d+)?$")


class JsonlTracer:
    def __init__(
        self,
        directory: Path,
        *,
        secrets: tuple[SecretStr, ...],
        rotation: Rotation,
        debug_enabled: bool,
        clock: Clock,
        debug_days: int = DEFAULT_DEBUG_DAYS,
    ) -> None:
        self._redactor = Redactor(secrets)
        self._clock = clock
        # 0700, because the debug log holds email text.
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        self._trace = RotatingLogFile(directory / "trace.jsonl", rotation)
        self._directory = directory
        self._rotation = rotation
        self._debug_enabled = debug_enabled
        self._debug_days = debug_days
        self._debug_file: RotatingLogFile | None = None
        self._debug_file_day: date | None = None

    def event(self, kind: str, **fields: object) -> None:
        if "ts" in fields:
            raise ValueError("the field name 'ts' is written by the tracer: rename it")
        record = self._redactor.value({"ts": self._timestamp(), "kind": kind, **fields})
        # ensure_ascii, because a character such as U+2028 ends a line for some readers, and each
        # event must stay exactly one line. allow_nan off, because NaN is not JSON.
        self._trace.append_line(json.dumps(record, ensure_ascii=True, allow_nan=False))

    def debug(self, text: str) -> None:
        if not self._debug_enabled:
            return
        self._debug_file_for_today().append_line(f"{self._timestamp()} {self._redactor.text(text)}")

    def _debug_file_for_today(self) -> RotatingLogFile:
        today = self._clock.now().date()
        if self._debug_file is None or self._debug_file_day != today:
            self._delete_debug_files_older_than(today)
            path = self._directory / f"debug-{today.isoformat()}.log"
            self._debug_file = RotatingLogFile(path, self._rotation)
            self._debug_file_day = today
        return self._debug_file

    def _delete_debug_files_older_than(self, today: date) -> None:
        oldest_kept = today - timedelta(days=self._debug_days)
        for path in self._directory.iterdir():
            named = DEBUG_FILE_PATTERN.match(path.name)
            if named and date.fromisoformat(named.group(1)) < oldest_kept:
                path.unlink(missing_ok=True)

    def _timestamp(self) -> str:
        return self._clock.now().isoformat(timespec="milliseconds")
