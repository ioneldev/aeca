from datetime import UTC, datetime
from pathlib import Path

from fakes.fixed_clock import FIXED_NOW, FixedClock

from aeca.adapters.heartbeat_file import HEARTBEAT_FILE_NAME, HeartbeatFile, read_heartbeat


def test_a_beat_writes_the_clock_time_as_utc_iso_8601(tmp_path: Path) -> None:
    HeartbeatFile(tmp_path, FixedClock()).beat()

    assert (tmp_path / HEARTBEAT_FILE_NAME).read_text().strip() == FIXED_NOW.isoformat()
    assert read_heartbeat(tmp_path / HEARTBEAT_FILE_NAME) == FIXED_NOW


def test_a_second_beat_replaces_the_first(tmp_path: Path) -> None:
    HeartbeatFile(tmp_path, FixedClock(datetime(2026, 1, 1, tzinfo=UTC))).beat()
    HeartbeatFile(tmp_path, FixedClock()).beat()

    assert read_heartbeat(tmp_path / HEARTBEAT_FILE_NAME) == FIXED_NOW
    assert [path.name for path in tmp_path.iterdir()] == [HEARTBEAT_FILE_NAME]


def test_a_missing_or_unreadable_file_reads_as_no_heartbeat(tmp_path: Path) -> None:
    assert read_heartbeat(tmp_path / HEARTBEAT_FILE_NAME) is None
    (tmp_path / HEARTBEAT_FILE_NAME).write_text("not a time")
    assert read_heartbeat(tmp_path / HEARTBEAT_FILE_NAME) is None
    (tmp_path / HEARTBEAT_FILE_NAME).write_text("2026-11-07T12:00:00")
    assert read_heartbeat(tmp_path / HEARTBEAT_FILE_NAME) is None
