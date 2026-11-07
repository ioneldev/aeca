"""An incident is a fault the manager must hear about once, not once per email. Its open state
lives in the state file, so a restart does not alert again."""

from dataclasses import dataclass
from datetime import UTC
from enum import StrEnum
from typing import Protocol

from aeca.application.executor import RECENT_SENT_SEARCHED
from aeca.application.ports import Clock, Mailbox
from aeca.application.state_ports import StateStore
from aeca.domain.ids import alert_id
from aeca.domain.labels import PROCESSED
from aeca.domain.outgoing import OutgoingMessage
from aeca.domain.templates import AlertNotice, alert


class IncidentKind(StrEnum):
    COST_CAP = "cost_cap"
    KEY_REFUSED = "key_refused"
    FAILURES_IN_A_ROW = "failures_in_a_row"


class IncidentReporter(Protocol):
    def report(self, kind: IncidentKind, detail: str) -> None:
        """Alerts the manager unless this incident is already open."""
        ...

    def clear(self, kind: IncidentKind) -> None: ...


@dataclass(frozen=True, slots=True)
class _Words:
    subject: str
    what_happened: str
    what_to_do: str


_WORDS = {
    IncidentKind.COST_CAP: _Words(
        subject="the day's model budget is spent",
        what_happened="AECA has spent its model budget for today (the UTC day). It makes no more "
        "model calls until tomorrow, and emails that need the model wait unread.",
        what_to_do="To carry on today, raise MAX_USD_PER_DAY in .env and restart AECA.",
    ),
    IncidentKind.KEY_REFUSED: _Words(
        subject="the Anthropic API key was refused",
        what_happened="Anthropic refused the API key, so AECA cannot read new emails. They wait "
        "unread.",
        what_to_do="Set ANTHROPIC_API_KEY to a valid key in .env and restart AECA.",
    ),
    IncidentKind.FAILURES_IN_A_ROW: _Words(
        subject="emails keep failing",
        what_happened="AECA could not process several emails in a row.",
        what_to_do="Look at audit.jsonl and trace.jsonl in the data volume. AECA retries each "
        "email on the next polls.",
    ),
}


@dataclass(frozen=True)
class IncidentsDeps:
    store: StateStore
    mailbox: Mailbox
    clock: Clock
    # MANAGER_ADDRESS, else the account itself.
    manager_address: str


class Incidents:
    def __init__(self, deps: IncidentsDeps) -> None:
        self._deps = deps

    def report(self, kind: IncidentKind, detail: str) -> None:
        if not self._deps.store.incident_open(kind.value):
            return
        try:
            self._send_once(kind, detail)
        except Exception:
            # Left open, a send that failed would never be tried again.
            self._deps.store.incident_close(kind.value)
            raise

    def clear(self, kind: IncidentKind) -> None:
        self._deps.store.incident_close(kind.value)

    # The id derives from the kind and the UTC day, and the account's recent sent mail is searched
    # for it first: a restart with the state file lost finds the alert already sent. The cost is
    # that one kind alerts at most once per UTC day.
    def _send_once(self, kind: IncidentKind, detail: str) -> None:
        message = self._alert(kind, detail)
        mailbox = self._deps.mailbox
        if mailbox.recently_sent_with_aeca_id(message.aeca_id, RECENT_SENT_SEARCHED) is not None:
            return
        sent = mailbox.send_draft(mailbox.create_draft(message))
        # So the inbox query never returns it, when the manager is the account itself.
        mailbox.modify(sent.message_id, add=[PROCESSED], remove=[])

    def _alert(self, kind: IncidentKind, detail: str) -> OutgoingMessage:
        words = _WORDS[kind]
        today = self._deps.clock.now().astimezone(UTC).date()
        return alert(
            AlertNotice(
                manager_address=self._deps.manager_address,
                aeca_id=alert_id(kind.value, today),
                subject=words.subject,
                text=f"{words.what_happened}\n\n{detail}\n\n{words.what_to_do}",
            )
        )
