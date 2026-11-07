import json
import unicodedata

from aeca.domain.audit import AuditLine

MAX_SUBJECT_CHARS = 200


def summary(line: AuditLine) -> str:
    """The one JSON object per line that `make logs` shows for an email.

    Redaction is the stdout writer's job, so this stays a pure formatting function."""
    fields = summary_fields(line)
    fields["subject"] = str(fields["subject"])[:MAX_SUBJECT_CHARS]
    return json.dumps(fields, ensure_ascii=True, separators=(",", ":"))


def summary_fields(line: AuditLine) -> dict[str, object]:
    """The summary before its subject is cut, so a writer can redact first and cut after."""
    return {
        "time": line.time.isoformat(),
        "outcome": line.outcome,
        "label": line.label,
        "message_id": line.message_id,
        "subject": _printable(line.subject),
        "approval_id": line.approval_id,
    }


# The subject comes from the sender, and stdout may be a terminal: an escape sequence in it
# would be run, not shown. Every control, format and line-separator character becomes a space.
def _printable(text: str) -> str:
    visible = (
        " " if unicodedata.category(char)[0] in "CZ" and char != " " else char for char in text
    )
    return " ".join("".join(visible).split())
