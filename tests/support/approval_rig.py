"""The inbox processor and the approval service together, with fakes at every edge.

Gmail and the calendar outlive a restart, and so does the state file unless the volume is wiped.
A tick runs the real service: the approval loop first, then the inbox loop.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fakes.fake_calendar import FakeCalendar
from fakes.fake_mailbox import FakeMailbox
from fakes.fixed_clock import FixedClock
from fakes.memory_audit import MemoryAudit
from fakes.recording_console import RecordingConsole
from fakes.recording_incidents import RecordingIncidents
from fakes.scripted_model import SERVED_MODEL, ScriptedModel
from support.decisions import decision_of
from support.emails import make_raw
from support.inbox_rig import ACCOUNT, BUCHAREST, PLAN_SETTINGS, InboxRig, RigOptions
from support.quiet_service import NoCopyLock, NoHeartbeat, nothing_to_start, quiet_watch

from aeca.adapters.sqlite_state import SqliteState
from aeca.application.approvals import ApprovalDeps, ApprovalService
from aeca.application.executor import Executor, ExecutorDeps
from aeca.application.service import Service, ServiceDeps
from aeca.application.startup import (
    ExecutorActionResumer,
    ResumerDeps,
    StartupDeps,
    StartUpWork,
    start_up,
)
from aeca.domain.answers import AnswerRules
from aeca.domain.approval import Approval
from aeca.domain.decision import Decision, Meeting, Window
from aeca.domain.facts import FactSettings
from aeca.domain.ids import approval_id
from aeca.domain.labels import Label
from aeca.domain.outgoing import OutgoingMessage
from aeca.domain.templates import APPROVAL_REQUEST_KIND
from aeca.domain.time_range import Interval

MANAGER = "boss@example.com"
COLLEAGUE = "colleague@example.com"
TUESDAY_3PM = datetime(2026, 11, 10, 15, 0, tzinfo=BUCHAREST)
TUESDAY_SLOT = Interval(TUESDAY_3PM, datetime(2026, 11, 10, 15, 30, tzinfo=BUCHAREST))
MEETING = Meeting(
    title="Sync", duration_minutes=None, windows=(Window(TUESDAY_3PM, None, "Tuesday at 3pm"),)
)
MEETING_REQUEST = decision_of(Label.MEETING_REQUEST, meeting=MEETING)
SOURCE_ID = "m-meeting"
SOURCE_THREAD = "t-meeting"
# After the request, which the fake mailbox dates a second after its 09:00 default.
FIRST_ANSWER_AT = datetime(2026, 11, 7, 10, 0, tzinfo=UTC)


@dataclass(frozen=True)
class ApprovalRigOptions:
    # MANAGER_ADDRESS as set; None means it is empty.
    manager_address: str | None = MANAGER
    internal_senders: frozenset[str] = frozenset()


class ApprovalRig:
    def __init__(self, state_dir: Path, options: ApprovalRigOptions | None = None) -> None:
        self.options = options or ApprovalRigOptions()
        self.inbox = InboxRig(
            state_dir,
            RigOptions(
                internal_senders=self.options.internal_senders,
                manager_address=self.manager_or_account,
            ),
        )
        self.console = RecordingConsole()
        self._answers_received = 0
        self.service = self._build()

    @property
    def manager_or_account(self) -> str:
        return self.options.manager_address or ACCOUNT

    @property
    def mailbox(self) -> FakeMailbox:
        return self.inbox.mailbox

    @property
    def calendar(self) -> FakeCalendar:
        return self.inbox.calendar

    @property
    def store(self) -> SqliteState:
        return self.inbox.store

    @property
    def audit(self) -> MemoryAudit:
        return self.inbox.audit

    @property
    def journal(self) -> list[str]:
        return self.inbox.journal

    def restart(self) -> None:
        self.inbox.restart()
        self.service = self._build()

    def restart_with_the_volume_wiped(self) -> None:
        self.inbox.restart_with_the_volume_wiped()
        self.service = self._build()

    def restart_through_start_up(self) -> None:
        """A restart that runs production's start-up before the first tick: the checks, the lock,
        then whatever start-up resolves."""
        self.restart()
        start_up(self._start_up_work())

    def restart_at(self, moment: datetime) -> None:
        """A restart whose clock reads `moment`, as if AECA had been down until then."""
        self.inbox.clock = FixedClock(moment)
        self.restart()

    def _executor(self) -> Executor:
        inbox = self.inbox
        return Executor(
            ExecutorDeps(
                mailbox=inbox.mailbox,
                calendar=inbox.calendar,
                store=inbox.store,
                tracer=inbox.tracer,
                manager_address=self.manager_or_account,
            )
        )

    def _start_up_work(self) -> StartUpWork:
        inbox = self.inbox
        return StartUpWork(
            checks=StartupDeps(
                mailbox=inbox.mailbox,
                calendar=inbox.calendar,
                model=ScriptedModel(),
                model_name=SERVED_MODEL,
                gmail_address=ACCOUNT,
                internal_senders=self.options.internal_senders,
                console=self.console,
            ),
            resumer=ExecutorActionResumer(
                ResumerDeps(
                    executor=self._executor(),
                    mailbox=inbox.mailbox,
                    fact_settings=self.service_fact_settings(),
                    tracer=inbox.tracer,
                )
            ),
            store=inbox.store,
            incidents=RecordingIncidents(),
            acquire_lock=lambda: None,
        )

    def _build(self) -> ApprovalService:
        inbox = self.inbox
        service = ApprovalService(
            ApprovalDeps(
                mailbox=inbox.mailbox,
                calendar=inbox.calendar,
                store=inbox.store,
                executor=self._executor(),
                audit=inbox.audit,
                tracer=inbox.tracer,
                clock=inbox.clock,
                plan_settings=PLAN_SETTINGS,
                answer_rules=AnswerRules(
                    account_address=ACCOUNT, manager_address=self.options.manager_address
                ),
                manager_address=self.manager_or_account,
                fact_settings=self.service_fact_settings(),
            )
        )
        inbox.use_approvals(service)
        return service

    def service_fact_settings(self) -> FactSettings:
        return FactSettings(
            account_address=ACCOUNT,
            internal_senders=self.options.internal_senders,
            bulk_senders=frozenset(),
        )

    # --- the flow ---

    def receive_meeting_request(self, decision: Decision = MEETING_REQUEST) -> str:
        """The meeting request arrives and the inbox loop opens its approval."""
        self.inbox.next_decision = decision
        self.inbox.receive(
            sender=COLLEAGUE,
            subject="Sync?",
            body="Tuesday at 3pm?",
            message_id=SOURCE_ID,
            thread_id=SOURCE_THREAD,
        )
        self.inbox.processor.process(SOURCE_ID)
        return SOURCE_ID

    def tick(self) -> None:
        Service(
            ServiceDeps(
                processor=self.inbox.processor,
                mailbox=self.inbox.mailbox,
                console=self.console,
                poll_seconds=30,
                approvals=self.service,
                watch=quiet_watch(),
                start_up=nothing_to_start,
                copy_lock=NoCopyLock(),
                heartbeat=NoHeartbeat(),
            )
        ).tick()

    def approval(self, attempt: int = 0) -> Approval:
        found = self.store.approval_get(approval_id(SOURCE_ID, attempt))
        assert found is not None, f"no approval for attempt {attempt}"
        return found

    def request_thread(self, attempt: int = 0) -> str:
        thread = self.approval(attempt).request_thread_id
        assert thread is not None, "the request was never recorded"
        return thread

    def requests_sent(self) -> list[OutgoingMessage]:
        return [message for message in self.mailbox.sent if message.kind == APPROVAL_REQUEST_KIND]

    def kinds_sent(self) -> list[str]:
        return [message.kind for message in self.mailbox.sent]

    def answer(
        self,
        text: str | None,
        *,
        sender: str = MANAGER,
        dmarc: str | None = "pass",
        label_ids: tuple[str, ...] = ("INBOX",),
        thread_id: str | None = None,
        **email: object,
    ) -> str:
        """A reply received after everything already in its thread."""
        self._answers_received += 1
        message_id = f"answer-{self._answers_received}"
        raw = make_raw(
            sender=sender,
            to=ACCOUNT,
            subject="Re: [AECA] Approve: meeting",
            body=text,
            dmarc=dmarc,
            label_ids=label_ids,
            message_id=message_id,
            thread_id=thread_id or self.request_thread(),
            internal_date=FIRST_ANSWER_AT + timedelta(minutes=self._answers_received),
            **email,  # type: ignore[arg-type]
        )
        self.mailbox.add(raw)
        return message_id

    def answer_from_account(self, text: str, **email: object) -> str:
        """Written in the AECA inbox: Gmail gives it SENT, and it carries no AECA header."""
        return self.answer(text, sender=ACCOUNT, dmarc=None, label_ids=("SENT", "INBOX"), **email)
