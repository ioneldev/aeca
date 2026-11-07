"""A whole Gmail account behind the googleapiclient shape: messages, drafts, labels, threads and
settings. It answers the way the cases for reset-test say Gmail does:

- messages.list leaves out SPAM and TRASH unless includeSpamTrash, includes DRAFT messages, lists
  newest first, and pages a few results at a time;
- its `q` matching is deliberately loose, as Gmail's tokenised search is: `from:X` matches X
  anywhere in the raw From, display name included, and `to:X` matches To or Cc, so a caller that
  skips an exact check of the header is caught;
- every write lands in the shared WriteJournal, and any method can be scripted to fail.

Each message carries a test-only made_by_rehearsal flag: the oracle the tests check against.
"""

from __future__ import annotations

import base64
import builtins
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from email import message_from_bytes
from email import policy as email_policy
from typing import Any

from fakes.fake_drafts_service import FakeDraftsService
from fakes.google_stub import http_error
from fakes.write_journal import CallSite, LazyRequest, ScriptedFailures, WriteJournal, WritesOnly

GMAIL = "gmail"
TRASH = "TRASH"
SPAM = "SPAM"
NOT_FOUND = 404
SNIPPET_PREFIX = "SNIPPET-OF-"
SYSTEM_LABELS = ("INBOX", "SENT", "DRAFT", "SPAM", "TRASH", "UNREAD", "STARRED", "IMPORTANT")


@dataclass
class StoredMessage:
    id: str
    thread_id: str
    labels: set[str]
    rfc822: bytes
    internal_date: datetime
    made_by_rehearsal: bool

    def header(self, name: str) -> str:
        parsed = message_from_bytes(self.rfc822, policy=email_policy.compat32)
        return " ".join(str(value) for value in parsed.get_all(name, []))


@dataclass
class FakeGmailAccount:
    journal: WriteJournal
    drafts_service: FakeDraftsService = field(
        default_factory=lambda: FakeDraftsService(page_size=2)
    )
    page_size: int = 2
    failures: ScriptedFailures = field(default_factory=ScriptedFailures)
    stored: dict[str, StoredMessage] = field(default_factory=dict)
    # Gmail's ids for the user labels, by name; a system label is its own id.
    user_labels: dict[str, str] = field(default_factory=dict)

    def add(self, message: StoredMessage) -> None:
        self.stored[message.id] = message

    def users(self) -> FakeGmailAccount:
        return self

    def messages(self) -> _Messages:
        return _Messages(self)

    def drafts(self) -> FakeDraftsService:
        return self.drafts_service

    def labels(self) -> _Labels:
        return _Labels(self)

    def threads(self) -> WritesOnly:
        return WritesOnly(self.journal, CallSite(GMAIL, "threads", "id"))

    def settings(self) -> WritesOnly:
        return WritesOnly(self.journal, CallSite(GMAIL, "settings", "id"))

    def draft_writes(self) -> list[str]:
        return [call for call in self.drafts_service.calls if not call.startswith("get")]


class _Messages:
    def __init__(self, account: FakeGmailAccount) -> None:
        self._account = account

    def list(
        self,
        *,
        userId: str,
        q: str | None = None,
        labelIds: builtins.list[str] | None = None,
        includeSpamTrash: bool = False,
        pageToken: str | None = None,
        maxResults: int | None = None,
    ) -> LazyRequest:
        query = _ListQuery(q, labelIds or [], includeSpamTrash)
        return LazyRequest(lambda: self._list(query, pageToken))

    def get(
        self,
        *,
        userId: str,
        id: str,
        format: str = "full",
        metadataHeaders: builtins.list[str] | None = None,
    ) -> LazyRequest:
        return LazyRequest(lambda: self._get(id, format))

    def trash(self, *, userId: str, id: str) -> LazyRequest:
        return LazyRequest(lambda: self._trash(id))

    def __getattr__(self, name: str) -> Callable[..., LazyRequest]:
        return WritesOnly(self._account.journal, CallSite(GMAIL, "messages", "id")).method(name)

    def _list(self, query: _ListQuery, page_token: str | None) -> dict[str, Any]:
        newest_first = sorted(
            self._account.stored.values(), key=lambda m: m.internal_date, reverse=True
        )
        matching = [m for m in newest_first if query.matches(m)]
        start = int(page_token or 0)
        page = matching[start : start + self._account.page_size]
        reply: dict[str, Any] = {
            "messages": [{"id": m.id, "threadId": m.thread_id} for m in page],
            "resultSizeEstimate": len(matching),
        }
        if start + self._account.page_size < len(matching):
            reply["nextPageToken"] = str(start + self._account.page_size)
        return reply

    def _get(self, message_id: str, format: str) -> dict[str, Any]:
        message = self._account.stored.get(message_id)
        if message is None:
            raise http_error(NOT_FOUND)
        reply: dict[str, Any] = {
            "id": message.id,
            "threadId": message.thread_id,
            "labelIds": sorted(message.labels),
            "internalDate": str(int(message.internal_date.timestamp() * 1000)),
            "snippet": SNIPPET_PREFIX + message.id,
        }
        if format == "raw":
            reply["raw"] = base64.urlsafe_b64encode(message.rfc822).decode("ascii")
        return reply

    def _trash(self, message_id: str) -> dict[str, Any]:
        self._account.journal.write(GMAIL, "messages.trash", message_id)
        self._account.failures.check("messages.trash", message_id)
        message = self._account.stored.get(message_id)
        if message is None:
            raise http_error(NOT_FOUND)
        # Whether Gmail keeps SPAM beside TRASH is unverified; keeping it is the harder case for a
        # finder that must leave Trash alone.
        message.labels.add(TRASH)
        message.labels.discard("INBOX")
        return {"id": message_id, "labelIds": sorted(message.labels)}


@dataclass(frozen=True)
class _ListQuery:
    q: str | None
    label_ids: list[str]
    include_spam_trash: bool

    def matches(self, message: StoredMessage) -> bool:
        if not self.include_spam_trash and message.labels & {SPAM, TRASH}:
            return False
        if not all(label in message.labels for label in self.label_ids):
            return False
        return all(_term_matches(term, message) for term in (self.q or "").split())


def _term_matches(term: str, message: StoredMessage) -> bool:
    operator, _, value = term.partition(":")
    wanted = value.lower()
    if operator == "from":
        return wanted in message.header("From").lower()
    if operator == "to":
        return wanted in (message.header("To") + " " + message.header("Cc")).lower()
    raise AssertionError(f"the fake does not model the search term {term!r}")


class _Labels:
    def __init__(self, account: FakeGmailAccount) -> None:
        self._account = account

    def list(self, *, userId: str) -> LazyRequest:
        labels = [{"id": name, "name": name, "type": "system"} for name in SYSTEM_LABELS]
        labels += [
            {"id": label_id, "name": name, "type": "user"}
            for name, label_id in self._account.user_labels.items()
        ]
        return LazyRequest(lambda: {"labels": labels})

    def __getattr__(self, name: str) -> Callable[..., LazyRequest]:
        return WritesOnly(self._account.journal, CallSite(GMAIL, "labels", "id")).method(name)
