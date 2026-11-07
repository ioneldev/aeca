from aeca.domain.audit import AuditLine


class MemoryAudit:
    """An AuditLog that keeps its lines in a list, for tests that check what was audited."""

    def __init__(self) -> None:
        self.lines: list[AuditLine] = []

    def write(self, line: AuditLine) -> None:
        self.lines.append(line)
