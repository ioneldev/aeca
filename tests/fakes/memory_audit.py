from aeca.domain.audit import AuditLine


class MemoryAudit:
    """An AuditLog that keeps its lines in a list, for tests that check what was audited."""

    def __init__(self, journal: list[str] | None = None) -> None:
        self.lines: list[AuditLine] = []
        # Shared with other fakes, so a test can assert the order across them.
        self._journal = journal if journal is not None else []
        self.failure: Exception | None = None

    def write(self, line: AuditLine) -> None:
        if self.failure is not None:
            raise self.failure
        self._journal.append("audit")
        self.lines.append(line)
