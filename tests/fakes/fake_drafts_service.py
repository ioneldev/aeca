"""A Gmail drafts service that keeps its drafts, so a test reads back what was written.

Like the real one, update keeps the draft id and gives the message inside it a new id, and
list returns every draft without their content.
"""

import base64
from collections.abc import Callable
from dataclasses import dataclass, field
from email import message_from_bytes
from email.message import EmailMessage
from typing import Any

from fakes.google_stub import http_error


@dataclass
class _Request:
    answer: Callable[[], dict[str, Any]]

    def execute(self) -> dict[str, Any]:
        return self.answer()


@dataclass
class FakeDraftsService:
    stored: dict[str, bytes] = field(default_factory=dict)
    calls: list[str] = field(default_factory=list)
    page_size: int = 100
    after_create: Callable[["FakeDraftsService"], None] | None = None
    # Raised by every drafts.list, as a Gmail outage would.
    list_failure: Exception | None = None
    # Called before each drafts.list answers, so a test can change the drafts between two reads.
    before_list: Callable[["FakeDraftsService"], None] | None = None
    _counter: int = 0

    def users(self) -> "FakeDraftsService":
        return self

    def drafts(self) -> "FakeDraftsService":
        return self

    def add(self, *, subject: str, body: str = "", draft_id: str | None = None) -> str:
        mime = EmailMessage()
        mime["Subject"] = subject
        mime.set_content(body or "x")
        if draft_id is None:
            return self._store(mime.as_bytes())
        self.stored[draft_id] = mime.as_bytes()
        return draft_id

    def raw_of(self, draft_id: str) -> bytes:
        return self.stored[draft_id]

    def list(self, *, userId: str, pageToken: str | None = None, **_: Any) -> _Request:
        return _Request(lambda: self._list(pageToken))

    def get(self, *, userId: str, id: str, format: str) -> _Request:
        self.calls.append(f"get:{format}")
        return _Request(lambda: self._get(id, format))

    def create(self, *, userId: str, body: dict[str, Any]) -> _Request:
        self.calls.append("create")
        return _Request(lambda: self._create(body))

    def update(self, *, userId: str, id: str, body: dict[str, Any]) -> _Request:
        self.calls.append("update")
        return _Request(lambda: self._update(id, body))

    def delete(self, **_: Any) -> _Request:
        self.calls.append("delete")
        return _Request(lambda: {})

    def send(self, **_: Any) -> _Request:
        self.calls.append("send")
        return _Request(lambda: {})

    def _create(self, body: dict[str, Any]) -> dict[str, Any]:
        created = {"id": self._store(_raw_in(body))}
        if self.after_create is not None:
            self.after_create(self)
        return created

    def _store(self, raw: bytes) -> str:
        self._counter += 1
        draft_id = f"draft-{self._counter}"
        self.stored[draft_id] = raw
        return draft_id

    def _list(self, page_token: str | None) -> dict[str, Any]:
        if self.list_failure is not None:
            raise self.list_failure
        if self.before_list is not None and page_token is None:
            self.before_list(self)
        ids = [*self.stored]
        start = int(page_token or 0)
        page = ids[start : start + self.page_size]
        reply: dict[str, Any] = {"drafts": [{"id": i, "message": {"id": f"m-{i}"}} for i in page]}
        if start + self.page_size < len(ids):
            reply["nextPageToken"] = str(start + self.page_size)
        return reply

    def _get(self, draft_id: str, format: str) -> dict[str, Any]:
        raw = self.stored.get(draft_id)
        if raw is None:
            raise http_error(404)
        if format == "raw":
            encoded = base64.urlsafe_b64encode(raw).decode("ascii")
            return {"id": draft_id, "message": {"id": f"m-{draft_id}", "raw": encoded}}
        parsed = message_from_bytes(raw)
        headers = [{"name": name, "value": str(value)} for name, value in parsed.items()]
        return {"id": draft_id, "message": {"payload": {"headers": headers}}}

    def _update(self, draft_id: str, body: dict[str, Any]) -> dict[str, Any]:
        if draft_id not in self.stored:
            raise http_error(404)
        self.stored[draft_id] = _raw_in(body)
        self._counter += 1
        return {"id": draft_id, "message": {"id": f"m-new-{self._counter}"}}


def _raw_in(body: dict[str, Any]) -> bytes:
    return base64.urlsafe_b64decode(body["message"]["raw"] + "==")
