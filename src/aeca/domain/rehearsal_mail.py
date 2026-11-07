"""Which mail a rehearsal left in the account, so that reset-test trashes that and nothing else.

The two rehearsal senders are Ionel's everyday addresses, and the evaluators' mail may share their
threads, their subjects, or the account itself as its sender. So every rule reads one message, and
only what Gmail vouches for: its single From with Gmail's own DMARC verdict, and AECA's headers on
mail the account itself wrote (spec §5 blocking case 9; tech-lead cases §1; ruling R53).
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import StrEnum

from aeca.domain.email import RawEmail, parse_message
from aeca.domain.facts import EmailFacts, FactSettings, extract_facts
from aeca.domain.ids import approval_id
from aeca.domain.labels import DRAFT, SENT
from aeca.domain.lease import LOCK_KIND
from aeca.domain.templates import APPROVAL_REQUEST_KIND

# Approvals have no maximum attempt: each superseded approval opens the next. Deriving an id costs
# one hash, so the search reaches far past any attempt a rehearsal could make.
APPROVAL_ATTEMPTS_SEARCHED = 21
RECIPIENT_HEADERS = ("To", "Cc", "Bcc")


class LeftoverRule(StrEnum):
    FROM_A_REHEARSAL_SENDER = "R1"
    SENT_ONLY_TO_REHEARSAL_SENDERS = "R2"
    LINKED_BY_A_DERIVED_APPROVAL_ID = "R3"
    IN_A_REHEARSAL_APPROVAL_THREAD = "R4"
    SEEDED_EVENT = "E1"
    DERIVED_EVENT = "E2"


# The rules that can recognise the approval request that starts a rehearsal approval thread.
REQUEST_RULES = frozenset(
    {LeftoverRule.SENT_ONLY_TO_REHEARSAL_SENDERS, LeftoverRule.LINKED_BY_A_DERIVED_APPROVAL_ID}
)


@dataclass(frozen=True, slots=True)
class MailItem:
    facts: EmailFacts
    # Every address in To, Cc and Bcc, lowercased; None when one of those headers can't be read.
    recipients: frozenset[str] | None

    @property
    def message_id(self) -> str:
        return self.facts.message_id


@dataclass(frozen=True, slots=True)
class MailMatch:
    item: MailItem
    rule: LeftoverRule


def mail_item_of(raw: RawEmail, account: str) -> MailItem:
    # No sender lists: they only decide `internal` and `bulk`, which no rule here reads.
    settings = FactSettings(
        account_address=account, internal_senders=frozenset(), bulk_senders=frozenset()
    )
    return MailItem(facts=extract_facts(raw, settings), recipients=_recipients(raw))


def is_from_a_rehearsal_sender(facts: EmailFacts, senders: frozenset[str]) -> bool:
    """Exactly one From address, equal to a rehearsal sender, with Gmail's DMARC pass for that
    address's own domain. A display name, a second address, a lookalike or a spoof never counts."""
    return (
        facts.single_sender
        and facts.sender in senders
        and facts.auth.dmarc == "pass"
        and facts.auth.dmarc_domain == _domain_of(facts.sender)
    )


def rehearsal_sources(items: Iterable[MailItem], senders: frozenset[str]) -> list[str]:
    """The Gmail ids AECA derived its approvals and events from, wherever the email is now."""
    return [item.message_id for item in items if is_from_a_rehearsal_sender(item.facts, senders)]


def derived_approval_ids(sources: Iterable[str]) -> frozenset[str]:
    return frozenset(
        approval_id(source, attempt)
        for source in sources
        for attempt in range(APPROVAL_ATTEMPTS_SEARCHED)
    )


def rehearsal_mail(items: Sequence[MailItem], senders: frozenset[str]) -> list[MailMatch]:
    """Every message a rule matches, whatever its folder: the caller decides what to do with
    Trash and drafts."""
    evidence = _Evidence(senders, derived_approval_ids(rehearsal_sources(items, senders)))
    direct = {
        item.message_id: rule for item in items if (rule := evidence.direct_rule(item)) is not None
    }
    approval_threads = _rehearsal_approval_threads(items, direct)
    matches = []
    for item in items:
        rule = direct.get(item.message_id) or _thread_rule(item, approval_threads)
        if rule is not None:
            matches.append(MailMatch(item=item, rule=rule))
    return matches


@dataclass(frozen=True, slots=True)
class _Evidence:
    senders: frozenset[str]
    approval_ids: frozenset[str]

    def direct_rule(self, item: MailItem) -> LeftoverRule | None:
        facts = item.facts
        if is_from_a_rehearsal_sender(facts, self.senders):
            return LeftoverRule.FROM_A_REHEARSAL_SENDER
        # Any sender can write X-AECA-* headers, so they count only on mail the account wrote.
        if not _written_by_the_account(facts) or facts.aeca_kind in (None, LOCK_KIND):
            return None
        if item.recipients and item.recipients <= self.senders:
            return LeftoverRule.SENT_ONLY_TO_REHEARSAL_SENDERS
        if facts.aeca_id in self.approval_ids:
            return LeftoverRule.LINKED_BY_A_DERIVED_APPROVAL_ID
        return None


# A hand reply typed in the AECA inbox carries no AECA header, and an evaluator's reply to their own
# copy's request looks the same. Only the thread tells them apart: it must have started with a
# request this module already matched.
def _rehearsal_approval_threads(
    items: Sequence[MailItem], direct: dict[str, LeftoverRule]
) -> frozenset[str]:
    first_by_thread: dict[str, MailItem] = {}
    for item in sorted(items, key=lambda each: (each.facts.received_at, each.message_id)):
        first_by_thread.setdefault(item.facts.thread_id, item)
    return frozenset(
        thread_id
        for thread_id, first in first_by_thread.items()
        if direct.get(first.message_id) in REQUEST_RULES
        and first.facts.aeca_kind == APPROVAL_REQUEST_KIND
    )


def _thread_rule(item: MailItem, approval_threads: frozenset[str]) -> LeftoverRule | None:
    if _written_by_the_account(item.facts) and item.facts.thread_id in approval_threads:
        return LeftoverRule.IN_A_REHEARSAL_APPROVAL_THREAD
    return None


def _written_by_the_account(facts: EmailFacts) -> bool:
    """SENT and DRAFT are labels only the account itself can give."""
    return bool(facts.label_ids & {SENT, DRAFT})


def _recipients(raw: RawEmail) -> frozenset[str] | None:
    message = parse_message(raw)
    try:
        return frozenset(
            address.addr_spec.lower()
            for name in RECIPIENT_HEADERS
            for header in message.get_all(name, [])
            for address in header.addresses
        )
    except Exception:
        # The stdlib parser raises from deep inside on a hostile header; unreadable counts as
        # unknown, and an unknown recipient never matches R2.
        return None


def _domain_of(address: str) -> str:
    return address.rpartition("@")[2]
