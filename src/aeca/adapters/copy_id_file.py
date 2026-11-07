"""The copy id on the volume, so a restart of the same installation is the same copy."""

import os
import uuid
from pathlib import Path

COPY_ID_FILE_NAME = "copy-id"


# A wiped volume makes a new copy, which waits out the stale window like any other machine: the
# alternative, an id kept anywhere another machine could share, would defeat the lock.
def load_or_create_copy_id(directory: Path) -> str:
    path = directory / COPY_ID_FILE_NAME
    existing = _read(path)
    if existing:
        return existing
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    created = uuid.uuid4().hex
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as file:
        file.write(created + "\n")
    return created


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""
