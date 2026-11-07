"""An approval opener that saves the approval as the real one does, and records each request, but
sends nothing."""

from aeca.application.ports import OpenApproval
from aeca.application.state_ports import StateStore
from aeca.domain.approval import Approval, ApprovalState

FAKE_APPROVAL_ID = "a-fake"


class RecordingApprovalOpener:
    def __init__(self, store: StateStore, journal: list[str] | None = None) -> None:
        self.opened: list[OpenApproval] = []
        self._store = store
        # Shared with other fakes, so a test can assert the order across them.
        self._journal = journal if journal is not None else []

    def open(self, request: OpenApproval) -> str:
        self.opened.append(request)
        self._journal.append("approval")
        self._store.approval_save(
            Approval(
                id=FAKE_APPROVAL_ID,
                source_message_id=request.source.message_id,
                source_thread_id=request.source.thread_id,
                attempt=request.attempt,
                actions=request.actions,
                state=ApprovalState.AWAITING,
                request_thread_id=None,
                summary=request.summary,
                holds=request.holds,
            )
        )
        return FAKE_APPROVAL_ID
