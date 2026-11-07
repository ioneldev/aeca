"""The heartbeat file on the volume, which the image's health check reads."""

import os
from datetime import datetime
from pathlib import Path

from aeca.application.ports import Clock

HEARTBEAT_FILE_NAME = "heartbeat"


class HeartbeatFile:
    def __init__(self, directory: Path, clock: Clock) -> None:
        self._path = directory / HEARTBEAT_FILE_NAME
        self._clock = clock
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)

    def beat(self) -> None:
        # Written aside and renamed, so a health check never reads half a time.
        pending = self._path.with_name(self._path.name + ".tmp")
        pending.write_text(self._clock.now().isoformat() + "\n", encoding="utf-8")
        os.replace(pending, self._path)


def read_heartbeat(path: Path) -> datetime | None:
    """The time written, or None when the file is missing or holds no aware time."""
    try:
        written = datetime.fromisoformat(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None
    return written if written.tzinfo is not None else None
