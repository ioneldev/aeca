"""An AppleScript runner that keeps what it was asked to run, so a test never starts osascript."""

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from aeca.tools.rehearse import ScriptFailed


@dataclass(frozen=True)
class RecordedRun:
    script: str
    arguments: tuple[str, ...]
    attachment_contents: dict[str, bytes]


class FakeScriptRunner:
    def __init__(self, *, fail_on_run: int | None = None, stderr: str = "boom") -> None:
        self.runs: list[RecordedRun] = []
        self._fail_on_run = fail_on_run
        self._stderr = stderr

    def run(self, script: str, arguments: Sequence[str] = ()) -> str:
        self.runs.append(RecordedRun(script, tuple(arguments), _read_attachments(arguments)))
        if len(self.runs) == self._fail_on_run:
            raise ScriptFailed(self._stderr)
        return ""


def _read_attachments(arguments: Sequence[str]) -> dict[str, bytes]:
    paths = [Path(argument) for argument in arguments[5:]]
    return {path.name: path.read_bytes() for path in paths if path.exists()}
