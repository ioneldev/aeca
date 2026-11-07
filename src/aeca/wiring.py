"""Builds the running service out of the settings. The one place that knows every adapter."""

import sys
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from aeca.adapters.anthropic_client import AnthropicClient
from aeca.adapters.audit_log import JsonlAuditLog
from aeca.adapters.clock import SystemClock
from aeca.adapters.console import JsonConsole
from aeca.adapters.google_services import GoogleServices, build_google_services
from aeca.adapters.models_file import load_models
from aeca.adapters.redactor import Redactor
from aeca.adapters.rotating_log import Rotation
from aeca.adapters.sqlite_state import SqliteState
from aeca.adapters.tracer import JsonlTracer
from aeca.application.budget import Budget
from aeca.application.decide import DecideDeps, decide
from aeca.application.process_email import InboxDeps, InboxProcessor
from aeca.application.service import Service, ServiceDeps
from aeca.domain.errors import ConfigError
from aeca.domain.facts import FactSettings
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
        clock=clock,
    )
    google = build_google_services(settings)
    budget = Budget(settings.max_usd_per_day)
    chosen_model = load_models().decision
    decide_deps = DecideDeps(
        model=AnthropicClient.from_api_key(_anthropic_key(settings), tracer),
        model_name=chosen_model.model,
        effort=chosen_model.effort,
        clock=clock,
        zone=_zone_of(settings, google),
        tracer=tracer,
        budget=budget,
    )
    mailbox = google.mailbox
    processor = InboxProcessor(
        InboxDeps(
            mailbox=mailbox,
            store=SqliteState(data_dir / STATE_FILE_NAME),
            audit=JsonlAuditLog(data_dir / AUDIT_FILE_NAME, secrets=secrets, rotation=LOG_ROTATION),
            tracer=tracer,
            decide=lambda facts: decide(facts, decide_deps),
            facts=FactSettings(
                account_address=settings.gmail_address,
                internal_senders=settings.internal_senders,
                bulk_senders=settings.bulk_senders,
            ),
            clock=clock,
            budget=budget,
        )
    )
    return Service(
        ServiceDeps(
            processor=processor,
            mailbox=mailbox,
            console=JsonConsole(_print_line, Redactor(secrets), clock),
            poll_seconds=settings.poll_seconds,
        )
    )


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
