"""trace.jsonl is evidence and the debug log holds email text: neither may hold a secret."""

import json
import stat
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import BaseModel

from aeca.adapters.clock import SystemClock
from aeca.adapters.rotating_log import Rotation
from aeca.adapters.tracer import REDACTED, JsonlTracer
from aeca.domain.secret import SecretStr

SECRET = "sk-ant-fake-0123456789"
ROOMY = Rotation(max_bytes=10_000_000, keep=5)
LINE_SEPARATOR = chr(0x2028)


class FixedClock:
    def now(self) -> datetime:
        return datetime(2026, 11, 9, 12, 0, tzinfo=UTC)


def tracer_in(
    directory: Path,
    *,
    rotation: Rotation = ROOMY,
    debug_enabled: bool = False,
    debug_days: int = 7,
) -> JsonlTracer:
    return JsonlTracer(
        directory,
        secrets=(SecretStr(SECRET),),
        rotation=rotation,
        debug_enabled=debug_enabled,
        debug_days=debug_days,
        clock=FixedClock(),
    )


def trace_lines(directory: Path) -> list[dict[str, object]]:
    text = (directory / "trace.jsonl").read_text(encoding="utf-8")
    return [json.loads(line) for line in text.splitlines()]


def test_an_event_is_one_json_line_with_time_and_kind(tmp_path: Path) -> None:
    tracer = tracer_in(tmp_path)

    tracer.event("model_call", tokens_in=10, latency_ms=250)

    [written] = trace_lines(tmp_path)
    assert written == {
        "ts": "2026-11-09T12:00:00.000+00:00",
        "kind": "model_call",
        "tokens_in": 10,
        "latency_ms": 250,
    }


def test_an_event_cannot_set_the_timestamp(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="ts"):
        tracer_in(tmp_path).event("x", ts="yesterday")


def test_a_newline_in_a_field_cannot_split_an_event(tmp_path: Path) -> None:
    tracer = tracer_in(tmp_path)

    tracer.event("warning", text="a\nb" + LINE_SEPARATOR)

    assert len((tmp_path / "trace.jsonl").read_text(encoding="utf-8").splitlines()) == 1


def test_secrets_are_redacted_in_audit_and_trace(tmp_path: Path) -> None:
    tracer = tracer_in(tmp_path, debug_enabled=True)

    tracer.event("google_call", error=f"bad key {SECRET}", nested={"k": [SECRET]})
    tracer.debug(f"full prompt with {SECRET} inside")

    everything = (tmp_path / "trace.jsonl").read_text(encoding="utf-8") + (
        tmp_path / "debug-2026-11-09.log"
    ).read_text(encoding="utf-8")
    assert SECRET not in everything
    assert everything.count(REDACTED) == 3


def test_a_secret_with_a_quote_is_redacted_before_json_escaping(tmp_path: Path) -> None:
    tricky = 'pa"ss\\word'
    tracer = JsonlTracer(
        tmp_path,
        secrets=(SecretStr(tricky),),
        rotation=ROOMY,
        debug_enabled=False,
        clock=FixedClock(),
    )

    tracer.event("x", text=f"the {tricky} leaked")

    assert trace_lines(tmp_path)[0]["text"] == f"the {REDACTED} leaked"


def test_debug_log_is_written_only_when_enabled(tmp_path: Path) -> None:
    quiet = tmp_path / "quiet"
    loud = tmp_path / "loud"

    tracer_in(quiet).debug("body text")
    tracer_in(loud, debug_enabled=True).debug("body text")

    assert not (quiet / "debug-2026-11-09.log").exists()
    assert "body text" in (loud / "debug-2026-11-09.log").read_text(encoding="utf-8")


def test_trace_rotates_keeping_five_files(tmp_path: Path) -> None:
    tracer = tracer_in(tmp_path, rotation=Rotation(max_bytes=200, keep=5))

    for number in range(50):
        tracer.event("step", number=number)

    assert len(list(tmp_path.glob("trace.jsonl*"))) == 6


def test_the_newest_events_stay_in_the_current_file_after_rotation(tmp_path: Path) -> None:
    tracer = tracer_in(tmp_path, rotation=Rotation(max_bytes=200, keep=2))

    for number in range(30):
        tracer.event("step", number=number)

    assert trace_lines(tmp_path)[-1]["number"] == 29


def test_the_system_clock_is_aware_and_utc() -> None:
    now = SystemClock().now()

    assert now.tzinfo is not None
    assert now.utcoffset() is not None and now.utcoffset().total_seconds() == 0  # type: ignore[union-attr]


def test_a_tracer_without_a_clock_is_refused(tmp_path: Path) -> None:
    with pytest.raises(TypeError, match="clock"):
        JsonlTracer(  # type: ignore[call-arg]
            tmp_path, secrets=(SecretStr(SECRET),), rotation=ROOMY, debug_enabled=False
        )


class Credentials(BaseModel):
    token: str


@pytest.mark.parametrize("secret", [SECRET, 'pa"ss\\word'])
def test_a_secret_inside_a_pydantic_model_is_redacted_by_exact_match(
    tmp_path: Path, secret: str
) -> None:
    tracer = JsonlTracer(
        tmp_path,
        secrets=(SecretStr(secret),),
        rotation=ROOMY,
        debug_enabled=False,
        clock=FixedClock(),
    )

    tracer.event("auth", credentials=Credentials(token=f"bearer {secret}"))

    assert secret not in (tmp_path / "trace.jsonl").read_text(encoding="utf-8")
    assert trace_lines(tmp_path)[0]["credentials"] == {"token": f"bearer {REDACTED}"}


def test_trace_and_debug_files_are_readable_by_their_owner_only(tmp_path: Path) -> None:
    tmp_path.chmod(0o755)
    tracer = tracer_in(tmp_path, rotation=Rotation(max_bytes=200, keep=2), debug_enabled=True)

    for number in range(10):
        tracer.event("step", number=number)
        tracer.debug(f"line {number} " + "x" * 100)

    modes = {
        path.name: stat.S_IMODE(path.stat().st_mode)
        for path in [*tmp_path.glob("trace.jsonl*"), *tmp_path.glob("debug-2026-11-09.log*")]
    }
    assert {
        "trace.jsonl",
        "trace.jsonl.1",
        "debug-2026-11-09.log",
        "debug-2026-11-09.log.1",
    } <= set(modes)
    assert set(modes.values()) == {0o600}


def _debug_files(directory: Path) -> list[str]:
    return sorted(path.name for path in directory.glob("debug*"))


def test_the_debug_log_is_off_by_default_and_old_files_are_removed(tmp_path: Path) -> None:
    quiet = tmp_path / "quiet"
    tracer_in(quiet).debug("body text")
    assert _debug_files(quiet) == []

    loud = tmp_path / "loud"
    loud.mkdir()
    for name in (
        "debug-2026-11-01.log",
        "debug-2026-11-02.log",
        "debug-2026-11-02.log.1",
        "debug-2026-11-09.log",
    ):
        (loud / name).write_text("old")
    (loud / "audit.jsonl").write_text("keep")
    (loud / "debug-notes.txt").write_text("keep")

    tracer_in(loud, debug_enabled=True, debug_days=7).debug("today")

    assert _debug_files(loud) == [
        "debug-2026-11-02.log",
        "debug-2026-11-02.log.1",
        "debug-2026-11-09.log",
        "debug-notes.txt",
    ]
