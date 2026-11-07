import json
from decimal import Decimal

from fakes.fixed_clock import FIXED_NOW, FixedClock

from aeca.adapters.console import JsonConsole
from aeca.adapters.json_log import MAX_SUBJECT_CHARS
from aeca.adapters.redactor import REDACTED, Redactor
from aeca.domain.audit import AuditLine
from aeca.domain.secret import SecretStr

SECRET = "sk-ant-this-is-the-secret"


def console_writing_to(lines: list[str]) -> JsonConsole:
    return JsonConsole(lines.append, Redactor((SecretStr(SECRET),)), FixedClock())


def an_audit_line(subject: str = "Hello") -> AuditLine:
    return AuditLine(
        time=FIXED_NOW,
        message_id="m1",
        thread_id="t1",
        sender="a@b.c",
        subject=subject,
        label="TASK",
        urgent=False,
        decided_by="rule",
        rules=(),
        actions=(),
        approval_id=None,
        approval_state=None,
        outcome="done",
        error=None,
        tokens_in=0,
        tokens_out=0,
        usd=Decimal(0),
    )


def test_an_email_prints_as_one_json_summary_line() -> None:
    lines: list[str] = []

    console_writing_to(lines).email(an_audit_line())

    assert len(lines) == 1
    assert json.loads(lines[0])["label"] == "TASK"


def test_a_service_event_is_one_json_line_with_a_time_and_a_kind() -> None:
    lines: list[str] = []

    console_writing_to(lines).event("backoff", seconds=60)

    assert json.loads(lines[0]) == {
        "ts": FIXED_NOW.isoformat(timespec="milliseconds"),
        "kind": "backoff",
        "seconds": 60,
    }


def test_a_secret_in_an_email_line_is_redacted() -> None:
    lines: list[str] = []

    console_writing_to(lines).email(an_audit_line(subject=f"key {SECRET} here"))

    assert SECRET not in lines[0]
    assert REDACTED in lines[0]


def test_a_secret_in_an_event_field_is_redacted() -> None:
    lines: list[str] = []

    console_writing_to(lines).event("backoff", reason=f"failed with {SECRET}")

    assert SECRET not in lines[0]
    assert REDACTED in lines[0]


def test_a_secret_straddling_the_subject_cut_leaves_no_head_behind() -> None:
    lines: list[str] = []
    subject = "x" * (MAX_SUBJECT_CHARS - 10) + SECRET

    console_writing_to(lines).email(an_audit_line(subject=subject))

    assert SECRET[:10] not in lines[0]


def test_a_secret_with_a_quote_and_a_backslash_is_redacted_in_the_printed_line() -> None:
    awkward = 'pa"ss\\word-with-escapes'
    lines: list[str] = []
    console = JsonConsole(lines.append, Redactor((SecretStr(awkward),)), FixedClock())

    console.email(an_audit_line(subject=f"see {awkward} here"))

    assert awkward not in json.loads(lines[0])["subject"]
    assert REDACTED in json.loads(lines[0])["subject"]
