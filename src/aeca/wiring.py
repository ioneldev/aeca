"""Builds the running service out of the settings. The one place that knows every adapter."""

import sys
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from aeca.adapters.anthropic_client import AnthropicClient
from aeca.adapters.audit_log import JsonlAuditLog
from aeca.adapters.clock import SystemClock
from aeca.adapters.console import JsonConsole
from aeca.adapters.copy_id_file import load_or_create_copy_id
from aeca.adapters.google_services import GoogleServices, build_google_services
from aeca.adapters.heartbeat_file import HeartbeatFile
from aeca.adapters.models_file import load_models
from aeca.adapters.redactor import Redactor
from aeca.adapters.rotating_log import Rotation
from aeca.adapters.sqlite_state import SqliteState
from aeca.adapters.tracer import JsonlTracer
from aeca.application.approvals import ApprovalDeps, ApprovalService
from aeca.application.budget import Budget, StoreLedger
from aeca.application.copy_lock import CopyLock, CopyLockDeps
from aeca.application.decide import DecideDeps, decide
from aeca.application.executor import Executor, ExecutorDeps
from aeca.application.incidents import Incidents, IncidentsDeps
from aeca.application.process_email import InboxDeps, InboxProcessor, InboxSettings
from aeca.application.service import Service, ServiceDeps
from aeca.application.startup import (
    ExecutorActionResumer,
    ResumerDeps,
    StartupDeps,
    StartUpWork,
    start_up,
)
from aeca.application.watch import FailureWatch, WatchDeps
from aeca.domain.answers import AnswerRules
from aeca.domain.errors import ConfigError
from aeca.domain.facts import FactSettings
from aeca.domain.planner import PlanSettings, WorkHours, parse_work_hours
from aeca.domain.policy import PolicySettings
from aeca.domain.secret import SecretStr
from aeca.settings import Settings

LOG_ROTATION = Rotation(max_bytes=10 * 1024 * 1024, keep=5)
STATE_FILE_NAME = "state.db"
AUDIT_FILE_NAME = "audit.jsonl"


def build(settings: Settings) -> Service:
    secrets = _secrets_of(settings)
    clock = SystemClock()
    data_dir = settings.data_dir
    tracer = JsonlTracer(
        data_dir,
        secrets=secrets,
        rotation=LOG_ROTATION,
        debug_enabled=settings.debug_log,
        debug_days=settings.debug_log_days,
        clock=clock,
    )
    google = build_google_services(settings)
    store = SqliteState(data_dir / STATE_FILE_NAME)
    budget = Budget(settings.max_usd_per_day, ledger=StoreLedger(store, clock))
    chosen_model = load_models().decision
    # Read once at startup: every plan and every message states this zone.
    zone = _zone_of(settings, google)
    model = AnthropicClient.from_api_key(_anthropic_key(settings), tracer)
    decide_deps = DecideDeps(
        model=model,
        model_name=chosen_model.model,
        effort=chosen_model.effort,
        clock=clock,
        zone=zone,
        tracer=tracer,
        budget=budget,
    )
    mailbox = google.mailbox
    audit = JsonlAuditLog(data_dir / AUDIT_FILE_NAME, secrets=secrets, rotation=LOG_ROTATION)
    inbox_settings = _inbox_settings(settings, zone)
    executor = Executor(
        ExecutorDeps(
            mailbox=mailbox,
            calendar=google.calendar,
            store=store,
            tracer=tracer,
            manager_address=settings.manager_or_account,
        )
    )
    approvals = ApprovalService(
        ApprovalDeps(
            mailbox=mailbox,
            calendar=google.calendar,
            store=store,
            executor=executor,
            audit=audit,
            tracer=tracer,
            clock=clock,
            plan_settings=inbox_settings.plan,
            answer_rules=AnswerRules(
                account_address=settings.gmail_address, manager_address=settings.manager_address
            ),
            manager_address=settings.manager_or_account,
            fact_settings=inbox_settings.facts,
        )
    )
    processor = InboxProcessor(
        InboxDeps(
            mailbox=mailbox,
            calendar=google.calendar,
            store=store,
            audit=audit,
            tracer=tracer,
            clock=clock,
            budget=budget,
            decide=lambda facts: decide(facts, decide_deps),
            executor=executor,
            approvals=approvals,
            settings=inbox_settings,
        )
    )
    console = JsonConsole(_print_line, Redactor(secrets), clock)
    incidents = Incidents(
        IncidentsDeps(
            store=store,
            mailbox=mailbox,
            clock=clock,
            manager_address=settings.manager_or_account,
        )
    )
    copy_lock = CopyLock(
        CopyLockDeps(
            lease=google.lease,
            clock=clock,
            copy_id=load_or_create_copy_id(data_dir),
            poll_seconds=settings.poll_seconds,
            force=settings.force_lock,
        )
    )
    startup_work = StartUpWork(
        checks=StartupDeps(
            mailbox=mailbox,
            calendar=google.calendar,
            model=model,
            model_name=chosen_model.model,
            gmail_address=settings.gmail_address,
            internal_senders=settings.internal_senders,
            console=console,
        ),
        resumer=ExecutorActionResumer(
            ResumerDeps(
                executor=executor,
                mailbox=mailbox,
                fact_settings=inbox_settings.facts,
                tracer=tracer,
            )
        ),
        store=store,
        incidents=incidents,
        acquire_lock=copy_lock.acquire,
    )
    return Service(
        ServiceDeps(
            processor=processor,
            mailbox=mailbox,
            console=console,
            poll_seconds=settings.poll_seconds,
            approvals=approvals,
            watch=FailureWatch(
                WatchDeps(
                    incidents=incidents,
                    budget=budget,
                    tracer=tracer,
                    alert_after_failures=settings.alert_after_failures,
                )
            ),
            start_up=lambda: start_up(startup_work),
            copy_lock=copy_lock,
            heartbeat=HeartbeatFile(data_dir, clock),
        )
    )


def _inbox_settings(settings: Settings, zone: ZoneInfo) -> InboxSettings:
    return InboxSettings(
        facts=FactSettings(
            account_address=settings.gmail_address,
            internal_senders=settings.internal_senders,
            bulk_senders=settings.bulk_senders,
        ),
        plan=PlanSettings(
            zone=zone,
            work_hours=_work_hours_of(settings),
            meeting_minutes=settings.meeting_minutes,
            buffer_minutes=settings.buffer_minutes,
            alternative_days=settings.alternative_days,
        ),
        policy=PolicySettings(sales_decline_needs_approval=settings.sales_decline_needs_approval),
    )


def _work_hours_of(settings: Settings) -> WorkHours:
    try:
        return parse_work_hours(settings.work_hours)
    except ValueError as error:
        raise ConfigError(
            f"WORK_HOURS is not valid: {error}.",
            next_action="Set WORK_HOURS like Mon-Fri 09:00-17:00, then run again.",
        ) from error


def _secrets_of(settings: Settings) -> tuple[SecretStr, ...]:
    held = (
        settings.google_client_secret,
        settings.google_refresh_token,
        settings.anthropic_api_key,
    )
    return tuple(secret for secret in held if secret is not None)


def _anthropic_key(settings: Settings) -> SecretStr:
    if settings.anthropic_api_key is None:
        raise ConfigError(
            "ANTHROPIC_API_KEY is not set.",
            next_action="Set ANTHROPIC_API_KEY in .env (see .env.example), then run again.",
        )
    return settings.anthropic_api_key


def _zone_of(settings: Settings, google: GoogleServices) -> ZoneInfo:
    name = settings.timezone or google.calendar.time_zone()
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as error:
        raise ConfigError(
            f"The time zone {name!r} is not known.",
            next_action="Set TIMEZONE to an IANA name such as Europe/Bucharest, then run again.",
        ) from error


def _print_line(line: str) -> None:
    sys.stdout.write(line + "\n")
    sys.stdout.flush()
