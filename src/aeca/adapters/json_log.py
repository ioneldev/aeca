import unicodedata

from aeca.domain.audit import AuditLine

MAX_SUBJECT_CHARS = 200


def summary(line: AuditLine) -> str:
    """The one line `make logs` shows for an email."""
    subject = _printable(line.subject)[:MAX_SUBJECT_CHARS]
    return f"{line.time.isoformat()} {line.outcome} {line.label} {line.message_id} {subject}"


# The subject comes from the sender, and stdout may be a terminal: an escape sequence in it
# would be run, not shown. Every control, format and line-separator character becomes a space.
def _printable(text: str) -> str:
    visible = (
        " " if unicodedata.category(char)[0] in "CZ" and char != " " else char for char in text
    )
    return " ".join("".join(visible).split())
