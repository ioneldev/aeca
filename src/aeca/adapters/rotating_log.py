from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Rotation:
    max_bytes: int
    keep: int


class RotatingLogFile:
    """Appends lines to one file, moving it to path.1, path.2 ... once it would pass max_bytes."""

    def __init__(self, path: Path, rotation: Rotation) -> None:
        self._path = path
        self._rotation = rotation
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)

    def append_line(self, line: str) -> None:
        encoded = (line + "\n").encode("utf-8", errors="backslashreplace")
        if self._would_overflow(len(encoded)):
            self._rotate()
        with self._path.open("ab") as file:
            file.write(encoded)

    def _would_overflow(self, incoming_bytes: int) -> bool:
        if not self._path.exists():
            return False
        current_bytes = self._path.stat().st_size
        # An empty file never rotates, so one line bigger than the limit is still written.
        return current_bytes > 0 and current_bytes + incoming_bytes > self._rotation.max_bytes

    def _rotate(self) -> None:
        self._numbered(self._rotation.keep).unlink(missing_ok=True)
        for number in range(self._rotation.keep - 1, 0, -1):
            source = self._numbered(number)
            if source.exists():
                source.replace(self._numbered(number + 1))
        self._path.replace(self._numbered(1))

    def _numbered(self, number: int) -> Path:
        return self._path.with_name(f"{self._path.name}.{number}")
