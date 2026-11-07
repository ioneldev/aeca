"""What runs before the first poll. Nothing here sends mail until every check has passed: Docker
restarts a failing container, so a mail from a failing start would be one per restart, and
Google suspends a new account that sends in bulk (spec §4.9)."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from aeca.application.executor import Executor
from aeca.application.failures import describe
from aeca.application.incidents import IncidentKind, IncidentReporter
from aeca.application.ports import Calendar, LLMClient, Mailbox, Tracer
from aeca.application.service import Console
from aeca.application.state_ports import StateStore
from aeca.domain.actions import ActionRecord
from aeca.domain.errors import ConfigError, EmailError
from aeca.domain.facts import FactSettings, extract_facts, same_gmail_account


@dataclass(frozen=True)
class StartupDeps:
    mailbox: Mailbox
    calendar: Calendar
    model: LLMClient
    model_name: str
    gmail_address: str
    internal_senders: frozenset[str]
    console: Console


class ActionResumer(Protocol):
    def resume(self, record: ActionRecord) -> bool:
        """True when the action was run through the executor's key checks."""
        ...


@dataclass(frozen=True)
class StartUpWork:
    checks: StartupDeps
    resumer: ActionResumer
    store: StateStore
    incidents: IncidentReporter
    # Raises ConfigError while another copy holds the inbox.
    acquire_lock: Callable[[], None]


def start_up(work: StartUpWork) -> None:
    startup_checks(work.checks)
    # Before the resume, which can send: a copy that must not run sends nothing.
    work.acquire_lock()
    # The key just worked, so a refusal recorded by an earlier run is over.
    work.incidents.clear(IncidentKind.KEY_REFUSED)
    resume_unfinished(work.resumer, work.store)


def startup_checks(deps: StartupDeps) -> None:
    """Raises AecaError with a next action, and sends nothing."""
    _require_the_token_to_be_for(deps)
    deps.calendar.time_zone()
    deps.model.check_access(deps.model_name)
    _warn_when_nobody_is_internal(deps)


def resume_unfinished(resumer: ActionResumer, store: StateStore) -> int:
    """Runs every pending or executing action again, before the first poll."""
    return sum(resumer.resume(record) for record in store.actions_unfinished())


def _require_the_token_to_be_for(deps: StartupDeps) -> None:
    account = deps.mailbox.address()
    if not same_gmail_account(account, deps.gmail_address):
        raise ConfigError(
            f"The token is for {account}, but GMAIL_ADDRESS is {deps.gmail_address}.",
            next_action="Set GMAIL_ADDRESS to the account the token is for, "
            "or run make auth for this account",
        )


def _warn_when_nobody_is_internal(deps: StartupDeps) -> None:
    if deps.internal_senders:
        return
    deps.console.event(
        "warning",
        setting="INTERNAL_SENDERS",
        message="INTERNAL_SENDERS is empty, so every urgent sender counts as internal",
    )


@dataclass(frozen=True)
class ResumerDeps:
    executor: Executor
    mailbox: Mailbox
    fact_settings: FactSettings
    tracer: Tracer


class ExecutorActionResumer:
    def __init__(self, deps: ResumerDeps) -> None:
        self._deps = deps

    def resume(self, record: ActionRecord) -> bool:
        try:
            source = extract_facts(
                self._deps.mailbox.get(record.source_message_id), self._deps.fact_settings
            )
            self._deps.executor.run((record.action,), source)
        except EmailError as error:
            self._deps.tracer.event("resume_failed", key=record.action.key, error=describe(error))
            return False
        return True
