from aeca.domain.audit import AuditLine


def summary(line: AuditLine) -> str:
    """The one line `make logs` shows for an email."""
    subject = " ".join(line.subject.split())
    return f"{line.time.isoformat()} {line.outcome} {line.label} {line.message_id} {subject}"
