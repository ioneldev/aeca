"""A stand-in for a googleapiclient service: chained calls are recorded, replies are canned."""

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import httplib2
from googleapiclient.errors import HttpError

Reply = dict[str, Any] | Exception | Callable[[dict[str, Any]], dict[str, Any]]


@dataclass(frozen=True)
class RecordedCall:
    path: str
    arguments: dict[str, Any]


@dataclass
class _Chain:
    stub: "GoogleStub"
    path: tuple[str, ...]
    arguments: dict[str, Any] = field(default_factory=dict)

    def __getattr__(self, name: str) -> Any:
        if name == "execute":
            return lambda: self.stub.answer(".".join(self.path), self.arguments)
        return _Chain(self.stub, (*self.path, name))

    def __call__(self, **arguments: Any) -> "_Chain":
        return _Chain(self.stub, self.path, arguments)


class GoogleStub:
    def __init__(self) -> None:
        self.calls: list[RecordedCall] = []
        self._replies: dict[str, list[Reply]] = {}

    def __getattr__(self, name: str) -> _Chain:
        return _Chain(self, (name,))

    def reply(self, path: str, *replies: Reply) -> None:
        """Successive calls get successive replies; the last one repeats."""
        self._replies[path] = list(replies)

    def answer(self, path: str, arguments: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(RecordedCall(path, arguments))
        queue = self._replies.get(path)
        if not queue:
            raise AssertionError(f"no canned reply for {path}")
        reply = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(reply, Exception):
            raise reply
        return reply(arguments) if callable(reply) else reply

    def calls_to(self, path: str) -> list[dict[str, Any]]:
        return [call.arguments for call in self.calls if call.path == path]

    def paths_called(self) -> set[str]:
        return {call.path for call in self.calls}


def http_error(status: int, reason: str | None = None) -> HttpError:
    errors = [{"reason": reason}] if reason else []
    content = json.dumps({"error": {"code": status, "message": "boom", "errors": errors}})
    return HttpError(httplib2.Response({"status": status}), content.encode())
