"""Opens an approval without asking anyone yet: it is saved, with its actions and holds, and waits.

The inbox rig's stand-in for the approval service, for tests about the inbox loop alone: no
request is sent. Nothing it saves can run: an awaiting approval's actions are never resumed.
"""

from aeca.application.ports import OpenApproval, Tracer
from aeca.application.state_ports import StateStore
from aeca.domain.approval import Approval, ApprovalState
from aeca.domain.ids import approval_id


class DeferredApprovalOpener:
    def __init__(self, store: StateStore, tracer: Tracer) -> None:
        self._store = store
        self._tracer = tracer

    def open(self, request: OpenApproval) -> str:
        source = request.source
        opened_id = approval_id(source.message_id, request.attempt)
        if self._store.approval_get(opened_id) is not None:
            return opened_id
        self._store.approval_save(
            Approval(
                id=opened_id,
                source_message_id=source.message_id,
                source_thread_id=source.thread_id,
                attempt=request.attempt,
                actions=request.actions,
                state=ApprovalState.AWAITING,
                request_thread_id=None,
                summary=request.summary,
                holds=request.holds,
            )
        )
        self._tracer.event(
            "approval_request_deferred", approval_id=opened_id, message_id=source.message_id
        )
        return opened_id
