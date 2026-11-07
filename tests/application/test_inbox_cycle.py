"""The real processor inside the real service, with fakes only at the edges."""

from decimal import Decimal

from fakes.fake_mailbox import FakeMailbox
from fakes.fixed_clock import FixedClock
from fakes.memory_audit import MemoryAudit
from fakes.memory_state import MemoryState
from fakes.recording_console import RecordingConsole
from fakes.recording_tracer import RecordingTracer
from support.emails import make_raw

from aeca.application.budget import Budget
from aeca.application.process_email import MAX_TRIES, InboxDeps, InboxProcessor
from aeca.application.service import Service, ServiceDeps
from aeca.domain.decision import Decision
from aeca.domain.errors import EmailError
from aeca.domain.facts import EmailFacts, FactSettings
from aeca.domain.labels import ERROR, PROCESSED, Label, ReplyKind

POISON_SUBJECT = "poison"


def decide_by_subject(facts: EmailFacts) -> Decision:
    if facts.subject == POISON_SUBJECT:
        raise EmailError("cannot read this", next_action="none")
    return Decision(
        label=Label.TASK,
        urgent=False,
        finance=False,
        meeting=None,
        reply=ReplyKind.NONE,
        urgent_draft=None,
        summary="s",
        suspected_injection=None,
        decided_by=Decision.fallback("x").decided_by,
        rules_fired=(),
        dropped=(),
    )


def build() -> tuple[Service, FakeMailbox, MemoryAudit, RecordingConsole]:
    mailbox = FakeMailbox()
    audit = MemoryAudit()
    console = RecordingConsole()
    processor = InboxProcessor(
        InboxDeps(
            mailbox=mailbox,
            store=MemoryState(),
            audit=audit,
            tracer=RecordingTracer(),
            decide=decide_by_subject,
            facts=FactSettings(
                account_address="aeca@gmail.com",
                internal_senders=frozenset(),
                bulk_senders=frozenset(),
            ),
            clock=FixedClock(),
            budget=Budget(Decimal(5)),
        )
    )
    service = Service(
        ServiceDeps(processor=processor, mailbox=mailbox, console=console, poll_seconds=0.01)
    )
    return service, mailbox, audit, console


def test_a_failing_email_is_retried_each_tick_while_the_good_one_after_it_is_done_once() -> None:
    service, mailbox, audit, console = build()
    mailbox.add(make_raw(message_id="bad", subject=POISON_SUBJECT))
    mailbox.add(make_raw(message_id="good", subject="fine"))

    results = [service.tick() for _ in range(MAX_TRIES + 1)]

    assert [(r.processed, r.failed) for r in results] == [(1, 1), (0, 1), (0, 1), (0, 0)]
    assert mailbox.added_labels("bad") == [ERROR, ERROR, ERROR, PROCESSED]
    assert mailbox.added_labels("good") == ["TASK", PROCESSED]
    assert [line.outcome for line in audit.lines].count("failed") == MAX_TRIES
    assert len(console.emails) == MAX_TRIES + 1
