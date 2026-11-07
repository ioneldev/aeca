"""Writes trace.jsonl, and debug.log when enabled, with every secret replaced by a marker."""

import json
from pathlib import Path

from aeca.adapters.clock import NowProvider
from aeca.adapters.redactor import REDACTED, Redactor
from aeca.adapters.rotating_log import RotatingLogFile, Rotation
from aeca.domain.secret import SecretStr

__all__ = ["REDACTED", "JsonlTracer"]


class JsonlTracer:
    def __init__(
        self,
        directory: Path,
        *,
        secrets: tuple[SecretStr, ...],
        rotation: Rotation,
        debug_enabled: bool,
        clock: NowProvider,
    ) -> None:
        self._redactor = Redactor(secrets)
        self._clock = clock
        # 0700, because debug.log holds email text.
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        self._trace = RotatingLogFile(directory / "trace.jsonl", rotation)
        self._debug = RotatingLogFile(directory / "debug.log", rotation) if debug_enabled else None

    def event(self, kind: str, **fields: object) -> None:
        if "ts" in fields:
            raise ValueError("the field name 'ts' is written by the tracer: rename it")
        record = self._redactor.value({"ts": self._timestamp(), "kind": kind, **fields})
        # ensure_ascii, because a character such as U+2028 ends a line for some readers, and each
        # event must stay exactly one line. allow_nan off, because NaN is not JSON.
        self._trace.append_line(json.dumps(record, ensure_ascii=True, allow_nan=False))

    def debug(self, text: str) -> None:
        if self._debug is not None:
            self._debug.append_line(f"{self._timestamp()} {self._redactor.text(text)}")

    def _timestamp(self) -> str:
        return self._clock.now().isoformat(timespec="milliseconds")
