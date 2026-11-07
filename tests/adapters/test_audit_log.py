"""audit.jsonl is read by the operator: one line per email, never the body, never a secret."""

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from aeca.adapters.audit_log import JsonlAuditLog
from aeca.adapters.json_log import summary
from aeca.adapters.rotating_log import Rotation
from aeca.domain.audit import AuditLine
from aeca.domain.secret import SecretStr

SECRET = "sk-ant-fake-0123456789"
ROOMY = Rotation(max_bytes=10_000_000, keep=5)


def line_of(**overrides: object) -> AuditLine:
    fields: dict[str, object] = {
        "time": datetime(2026, 11, 9, 12, 0, tzinfo=UTC),
        "message_id": "m1",
        "thread_id": "t1",
        "sender": "Jürgen Müller <j@example.de>",
        "subject": "Änderung",
        "label": "TASK",
        "urgent": False,
        "decided_by": "model",
        "rules": ("known_sender",),
        "actions": (("apply_label", "m1:label", "done"),),
        "approval_id": None,
        "approval_state": None,
        "outcome": "done",
        "error": None,
        "tokens_in": 120,
        "tokens_out": 30,
        "usd": Decimal("0.0042"),
    }
    return AuditLine(**{**fields, **overrides})  # type: ignore[arg-type]


def audit_in(tmp_path: Path, rotation: Rotation = ROOMY) -> JsonlAuditLog:
    return JsonlAuditLog(tmp_path / "audit.jsonl", secrets=(SecretStr(SECRET),), rotation=rotation)


def written_lines(tmp_path: Path) -> list[dict[str, object]]:
    text = (tmp_path / "audit.jsonl").read_text(encoding="utf-8")
    return [json.loads(line) for line in text.splitlines()]


def test_audit_line_never_holds_body_text(tmp_path: Path) -> None:
    body = "BODY-MARKER please wire the money"
    audit = audit_in(tmp_path)

    audit.write(line_of(subject="Invoice"))

    assert "BODY-MARKER" not in (tmp_path / "audit.jsonl").read_text(encoding="utf-8")
    assert body not in json.dumps(written_lines(tmp_path))
    assert "body" not in written_lines(tmp_path)[0]


def test_a_line_is_one_json_object_with_the_facts(tmp_path: Path) -> None:
    audit_in(tmp_path).write(line_of())

    [written] = written_lines(tmp_path)

    assert written["message_id"] == "m1"
    assert written["time"] == "2026-11-09T12:00:00+00:00"
    assert written["actions"] == [["apply_label", "m1:label", "done"]]
    assert written["usd"] == "0.0042"
    assert written["sender"] == "Jürgen Müller <j@example.de>"


def test_secrets_are_redacted_in_the_audit(tmp_path: Path) -> None:
    audit = audit_in(tmp_path)

    audit.write(line_of(error=f"401 for key {SECRET}", subject=f"re {SECRET}"))

    assert SECRET not in (tmp_path / "audit.jsonl").read_text(encoding="utf-8")
    assert "[REDACTED]" in (tmp_path / "audit.jsonl").read_text(encoding="utf-8")


def test_sender_and_subject_are_bounded_to_200_characters(tmp_path: Path) -> None:
    audit_in(tmp_path).write(line_of(sender="s" * 500, subject="x" * 500))

    [written] = written_lines(tmp_path)

    assert len(str(written["sender"])) == 200
    assert len(str(written["subject"])) == 200


def test_a_secret_cut_by_the_bound_does_not_leak_its_head(tmp_path: Path) -> None:
    subject = "x" * 190 + SECRET

    audit_in(tmp_path).write(line_of(subject=subject))

    assert SECRET[:10] not in str(written_lines(tmp_path)[0]["subject"])


def test_the_audit_rotates_keeping_five_files(tmp_path: Path) -> None:
    audit = audit_in(tmp_path, Rotation(max_bytes=400, keep=5))

    for number in range(40):
        audit.write(line_of(message_id=f"m{number}"))

    assert len(list(tmp_path.glob("audit.jsonl*"))) <= 6


def test_summary_is_one_line_with_outcome_label_and_subject() -> None:
    text = summary(line_of(subject="Line one\nline two"))

    assert "\n" not in text
    assert "done" in text and "TASK" in text and "Line one" in text


@pytest.mark.parametrize("outcome", ["done", "awaiting_approval", "failed", "skipped"])
def test_summary_names_every_outcome(outcome: str) -> None:
    assert outcome in summary(line_of(outcome=outcome))


def test_a_unicode_line_separator_in_a_subject_cannot_split_an_audit_line(tmp_path: Path) -> None:
    audit = audit_in(tmp_path)

    audit.write(line_of(subject="a" + chr(0x2028) + "b" + chr(0x85) + "c"))
    audit.write(line_of(message_id="m2"))

    assert len((tmp_path / "audit.jsonl").read_text(encoding="utf-8").splitlines()) == 2


def test_summary_strips_terminal_escapes_and_bounds_the_subject() -> None:
    text = summary(line_of(subject="\x1b[31mred\x1b[0m" + "x" * 500))

    assert "\x1b" not in text
    assert len(text.split(" ", 5)[5]) <= 200
