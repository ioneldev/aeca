"""An approval opener that only records what it was asked to open."""

from aeca.application.ports import OpenApproval

FAKE_APPROVAL_ID = "a-fake"


class RecordingApprovalOpener:
    def __init__(self, journal: list[str] | None = None) -> None:
        self.opened: list[OpenApproval] = []
        # Shared with other fakes, so a test can assert the order across them.
        self._journal = journal if journal is not None else []

    def open(self, request: OpenApproval) -> str:
        self.opened.append(request)
        self._journal.append("approval")
        return FAKE_APPROVAL_ID
