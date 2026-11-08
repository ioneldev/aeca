"""The two parts of the state store the inbox processor uses, kept in memory."""

from collections import Counter

from aeca.domain.approval import Approval


class MemoryState:
    def __init__(self) -> None:
        self._attempts: Counter[str] = Counter()
        self.approvals_by_thread: dict[str, Approval] = {}

    def attempts_increment(self, message_id: str) -> int:
        self._attempts[message_id] += 1
        return self._attempts[message_id]

    def approval_by_request_thread(self, thread_id: str) -> Approval | None:
        return self.approvals_by_thread.get(thread_id)
