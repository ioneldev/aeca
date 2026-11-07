"""The copy lock as a Gmail draft titled `AECA lock`, renewed in place (day-1 spike 14).

Drafts are found with drafts.list, which is consistent, unlike search. The lock draft has no
recipient and AECA never sends it: Gmail would refuse to, and no send path is ever handed its id.
"""

import base64
from datetime import datetime
from email import message_from_bytes, policy
from email.message import EmailMessage
from typing import Any

from aeca.adapters.google_errors import execute, execute_unless_missing
from aeca.domain.errors import LockLost
from aeca.domain.lease import (
    LeaseHolding,
    LeaseOutcome,
    LeaseRequest,
    LeaseResult,
    decide_acquisition,
)

LOCK_SUBJECT = "AECA lock"
LOCK_KIND = "lock"
USER = "me"
COPY_ID_FIELD = "copy_id"
HEARTBEAT_FIELD = "heartbeat"


class GmailLease:
    def __init__(self, service: Any) -> None:
        self._drafts = service.users().drafts()
        self._lock_draft_id: str | None = None

    def acquire(self, request: LeaseRequest) -> LeaseResult:
        found = self._find_lock()
        holding = found[1] if found else None
        outcome = decide_acquisition(holding, request)
        if outcome is LeaseOutcome.HELD_BY_ANOTHER:
            return LeaseResult(outcome=outcome, holding=holding)
        mine = LeaseHolding(request.copy_id, request.now)
        if found is not None:
            self._write(found[0], mine)
            return LeaseResult(outcome=outcome, holding=holding)
        return self._create_lock(mine, outcome)

    def still_held(self, copy_id: str) -> bool:
        found = self._find_lock()
        return found is not None and found[1] is not None and found[1].copy_id == copy_id

    def renew(self, copy_id: str, now: datetime) -> None:
        found = self._find_lock()
        if found is None or found[1] is None or found[1].copy_id != copy_id:
            raise LockLost.for_a_lost_lock()
        if not self._write(found[0], LeaseHolding(copy_id, now)):
            raise LockLost.for_a_lost_lock()

    # Two copies can create a lock at the same moment. Every copy then reads the same answer: the
    # lowest draft id is the lock. The other draft is left alone, because a deletion that races a
    # new copy is worse than a leftover.
    def _create_lock(self, mine: LeaseHolding, outcome: LeaseOutcome) -> LeaseResult:
        self._write(None, mine)
        created_id = self._lock_draft_id
        lowest_id = self._lowest_lock_draft_id()
        if lowest_id == created_id:
            return LeaseResult(outcome=outcome, holding=None)
        self._lock_draft_id = None
        found = self._find_lock()
        holding = found[1] if found else None
        return LeaseResult(outcome=LeaseOutcome.HELD_BY_ANOTHER, holding=holding)

    def _find_lock(self) -> tuple[str, LeaseHolding | None] | None:
        """The lock draft's id and what it says, None in the holding when its body is unreadable."""
        draft_id = self._remembered_or_listed_lock()
        if draft_id is None:
            return None
        reply = execute_unless_missing(self._drafts.get(userId=USER, id=draft_id, format="raw"))
        if reply is None:
            self._lock_draft_id = None
            return None
        self._lock_draft_id = draft_id
        return draft_id, _holding_of(reply)

    def _remembered_or_listed_lock(self) -> str | None:
        if self._lock_draft_id is not None:
            return self._lock_draft_id
        return self._lowest_lock_draft_id()

    def _lowest_lock_draft_id(self) -> str | None:
        lock_ids = [i for i in self._listed_draft_ids() if self._subject_of(i) == LOCK_SUBJECT]
        return min(lock_ids, default=None)

    def _listed_draft_ids(self) -> list[str]:
        ids: list[str] = []
        page_token: str | None = None
        while True:
            extra = {"pageToken": page_token} if page_token else {}
            reply = execute(self._drafts.list(userId=USER, **extra))
            ids.extend(draft["id"] for draft in reply.get("drafts", []))
            page_token = reply.get("nextPageToken")
            if not page_token:
                return ids

    def _subject_of(self, draft_id: str) -> str | None:
        reply = execute_unless_missing(
            self._drafts.get(userId=USER, id=draft_id, format="metadata")
        )
        if reply is None:
            return None
        headers = reply.get("message", {}).get("payload", {}).get("headers", [])
        return next((h["value"] for h in headers if h["name"].lower() == "subject"), None)

    def _write(self, draft_id: str | None, holding: LeaseHolding) -> bool:
        """False when the draft to update is gone."""
        body = {"message": {"raw": _encode(_lock_mime(holding))}}
        if draft_id is None:
            self._lock_draft_id = str(execute(self._drafts.create(userId=USER, body=body))["id"])
            return True
        updated = execute_unless_missing(
            self._drafts.update(userId=USER, id=draft_id, body={"id": draft_id, **body})
        )
        return updated is not None


def _lock_mime(holding: LeaseHolding) -> bytes:
    mime = EmailMessage(policy=policy.SMTP)
    mime["Subject"] = LOCK_SUBJECT
    mime["X-AECA-Kind"] = LOCK_KIND
    mime.set_content(
        f"{COPY_ID_FIELD}: {holding.copy_id}\n{HEARTBEAT_FIELD}: {holding.heartbeat.isoformat()}\n",
        charset="utf-8",
        cte="quoted-printable",
    )
    return mime.as_bytes()


def _holding_of(reply: dict[str, Any]) -> LeaseHolding | None:
    raw = base64.urlsafe_b64decode(reply["message"]["raw"] + "==")
    body = message_from_bytes(raw, policy=policy.default).get_body(preferencelist=("plain",))
    if body is None:
        return None
    fields = dict(
        line.split(": ", 1) for line in str(body.get_content()).splitlines() if ": " in line
    )
    try:
        heartbeat = datetime.fromisoformat(fields[HEARTBEAT_FIELD])
        copy_id = fields[COPY_ID_FIELD]
    except (KeyError, ValueError):
        return None
    return LeaseHolding(copy_id, heartbeat) if heartbeat.tzinfo is not None else None


def _encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii")
