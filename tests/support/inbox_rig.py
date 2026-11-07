"""The real inbox processor with fakes at every edge, and a state file that outlives a restart
the way the data volume does. Gmail and the calendar outlive AECA too, so a restart keeps them."""

from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from fakes.fake_calendar import FakeCalendar
from fakes.fake_mailbox import FakeMailbox
from fakes.fixed_clock import FixedClock
from fakes.memory_audit import MemoryAudit
from fakes.recording_approvals import RecordingApprovalOpener
from fakes.recording_tracer import RecordingTracer
from fakes.scripted_model import response_with_text
from support.decisions import decision_of
from support.emails import make_raw

from aeca.adapters.sqlite_state import SqliteState
from aeca.application.budget import Budget
from aeca.application.deferred_approval import DeferredApprovalOpener
from aeca.application.executor import Executor, ExecutorDeps
from aeca.application.ports import ApprovalOpener
from aeca.application.process_email import InboxDeps, InboxProcessor, InboxSettings
from aeca.domain.decision import Decision
from aeca.domain.facts import EmailFacts, FactSettings
from aeca.domain.labels import Label
from aeca.domain.planner import PlanSettings, parse_work_hours
from aeca.domain.policy import PolicySettings

ACCOUNT = "aeca@gmail.com"
BUCHAREST = ZoneInfo("Europe/Bucharest")
PLAN_SETTINGS = PlanSettings(
    zone=BUCHAREST,
    work_hours=parse_work_hours("Mon-Fri 09:00-17:00"),
    meeting_minutes=30,
    buffer_minutes=10,
    alternative_days=5,
)


@dataclass(frozen=True)
class RigOptions:
    internal_senders: frozenset[str] = frozenset()
    manager_address: str = ACCOUNT
    sales_decline_needs_approval: bool = False


class InboxRig:
    def __init__(self, state_dir: Path, options: RigOptions | None = None) -> None:
        self.options = options or RigOptions()
        self.journal: list[str] = []
        self.mailbox = FakeMailbox(ACCOUNT, journal=self.journal)
        self.calendar = FakeCalendar()
        self.audit = MemoryAudit(self.journal)
        self.tracer = RecordingTracer()
        self.clock = FixedClock()
        self.budget = Budget(Decimal(5))
        self.next_decision: Decision | Exception = decision_of(Label.TASK)
        self.decisions_by_subject: dict[str, Decision] = {}
        self.decided: list[EmailFacts] = []
        # When set, each decision pays for one model call, as decide() does.
        self.decisions_cost_money = False
        self._state_dir = state_dir
        state_dir.mkdir(parents=True, exist_ok=True)
        self._volumes = 0
        # Outlives a restart, as the manager's mailbox would; None means the deferred opener.
        self.recording_approvals: RecordingApprovalOpener | None = None
        self.store = self._new_volume()
        self.processor = self._build()

    def record_approvals(self) -> RecordingApprovalOpener:
        self.recording_approvals = RecordingApprovalOpener(self.journal)
        self.processor = self._build()
        return self.recording_approvals

    def receive(self, **email: object) -> str:
        raw = make_raw(**email)  # type: ignore[arg-type]
        self.mailbox.add(raw)
        return raw.message_id

    def restart(self) -> None:
        """A new process on the same volume: every object is rebuilt, the state file is kept."""
        self.store = SqliteState(self._volume_path(self._volumes))
        self.processor = self._build()

    def restart_with_the_volume_wiped(self) -> None:
        self.store = self._new_volume()
        self.processor = self._build()

    def _new_volume(self) -> SqliteState:
        self._volumes += 1
        return SqliteState(self._volume_path(self._volumes))

    def _volume_path(self, number: int) -> Path:
        return self._state_dir / f"state-{number}.db"

    def _build(self) -> InboxProcessor:
        executor = Executor(
            ExecutorDeps(
                mailbox=self.mailbox,
                calendar=self.calendar,
                store=self.store,
                tracer=self.tracer,
                manager_address=self.options.manager_address,
            )
        )
        return InboxProcessor(
            InboxDeps(
                mailbox=self.mailbox,
                calendar=self.calendar,
                store=self.store,
                audit=self.audit,
                tracer=self.tracer,
                clock=self.clock,
                budget=self.budget,
                decide=self._decide,
                executor=executor,
                approvals=self._approvals(),
                settings=InboxSettings(
                    facts=FactSettings(
                        account_address=ACCOUNT,
                        internal_senders=self.options.internal_senders,
                        bulk_senders=frozenset(),
                    ),
                    plan=PLAN_SETTINGS,
                    policy=PolicySettings(
                        sales_decline_needs_approval=self.options.sales_decline_needs_approval
                    ),
                ),
            )
        )

    def _approvals(self) -> ApprovalOpener:
        if self.recording_approvals is not None:
            return self.recording_approvals
        return DeferredApprovalOpener(self.store, self.tracer)

    def _decide(self, facts: EmailFacts) -> Decision:
        self.decided.append(facts)
        if self.decisions_cost_money:
            self.budget.charge(response_with_text("paid"))
        if facts.subject in self.decisions_by_subject:
            return self.decisions_by_subject[facts.subject]
        if isinstance(self.next_decision, Exception):
            raise self.next_decision
        return self.next_decision
