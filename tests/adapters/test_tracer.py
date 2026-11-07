"""trace.jsonl is evidence and debug.log holds email text: neither may hold a secret."""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

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
    directory: Path, *, rotation: Rotation = ROOMY, debug_enabled: bool = False
) -> JsonlTracer:
    return JsonlTracer(
        directory,
        secrets=(SecretStr(SECRET),),
        rotation=rotation,
        debug_enabled=debug_enabled,
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
        tmp_path / "debug.log"
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

    assert not (quiet / "debug.log").exists()
    assert "body text" in (loud / "debug.log").read_text(encoding="utf-8")


def test_trace_rotates_keeping_five_files(tmp_path: Path) -> None:
    tracer = tracer_in(tmp_path, rotation=Rotation(max_bytes=200, keep=5))

    for number in range(50):
        tracer.event("step", number=number)

    assert 2 <= len(list(tmp_path.glob("trace.jsonl*"))) <= 6


def test_the_newest_events_stay_in_the_current_file_after_rotation(tmp_path: Path) -> None:
    tracer = tracer_in(tmp_path, rotation=Rotation(max_bytes=200, keep=2))

    for number in range(30):
        tracer.event("step", number=number)

    assert trace_lines(tmp_path)[-1]["number"] == 29


def test_the_system_clock_is_aware_and_utc() -> None:
    now = SystemClock().now()

    assert now.tzinfo is not None
    assert now.utcoffset() is not None and now.utcoffset().total_seconds() == 0  # type: ignore[union-attr]
