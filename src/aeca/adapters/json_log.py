import unicodedata

from aeca.domain.audit import AuditLine

MAX_SUBJECT_CHARS = 200


def summary_fields(line: AuditLine) -> dict[str, object]:
    """The facts `make logs` shows for an email, before the subject is cut, so the stdout writer
    can redact first and cut after."""
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
