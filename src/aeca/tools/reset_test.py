"""`aeca reset-test`: moves what a rehearsal left in the handover account to Trash, and cancels its
events. It prints the plan and stops unless --yes is given.

It only moves mail to Trash and cancels events, touches only what the rehearsal made, refuses while
any copy of AECA may hold the lock, and runs dry first (spec §5, blocking case 9). The lock is a
courtesy: the evaluators' mail sits in the inbox while no copy runs, so the rules in
domain/rehearsal_mail.py are what keep it safe.
"""

import re
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Protocol

from aeca.adapters.google_reset import CalendarEntry
from aeca.application.executor import MAX_EVENT_ATTEMPTS
from aeca.application.ports import Clock
from aeca.domain.email import RawEmail
from aeca.domain.errors import AecaError, ConfigError
from aeca.domain.facts import same_gmail_account
from aeca.domain.ids import event_id
from aeca.domain.labels import DRAFT, INBOX, PROCESSED
from aeca.domain.rehearsal_mail import (
    LeftoverRule,
    MailItem,
    MailMatch,
    mail_item_of,
    rehearsal_mail,
    rehearsal_sources,
)
from aeca.domain.reset_lock import LockCheck, LockReading, blocking_lock
from aeca.settings import RehearsalSenders, Settings
from aeca.tools.reset_plan import PlannedEvent, ResetPlan, SenderTally, plan_lines
from aeca.tools.seed_calendar import TEST_TAG

TRASH = "TRASH"
[(SEED_TAG_NAME, SEED_TAG_VALUE)] = TEST_TAG.items()
SINGLE_ADDRESS = re.compile(r"[^\s@,;<>()\"']+@[^\s@,;<>()\"']+\.[^\s@,;<>()\"']+")
STOP_EVERY_COPY = "Stop every copy of AECA with make stop"


class ResetMailbox(Protocol):
    def every_message(self) -> Iterable[RawEmail]: ...

    def label_ids_by_name(self) -> Mapping[str, str]: ...

    def lock_readings(self) -> list[LockReading]: ...

    def trash(self, message_id: str) -> None: ...


class ResetCalendar(Protocol):
    def tagged_events(self, name: str, value: str) -> list[CalendarEntry]: ...

    def event(self, event_id: str) -> CalendarEntry | None: ...

    def cancel(self, event_id: str) -> None: ...


@dataclass(frozen=True)
class ResetDeps:
    mailbox: ResetMailbox
    calendar: ResetCalendar
    account: str
    senders: RehearsalSenders
    clock: Clock
    lock_window: timedelta
    show: Callable[[str], None]


class ResetRefused(AecaError):
    """reset-test changed nothing, because running now could touch what it must not."""


class ResetIncomplete(AecaError):
    """Some writes failed; every other item was still done, and a rerun finds only what is left."""


def rehearsal_senders_to_reset(settings: Settings) -> RehearsalSenders:
    """Checked before any Google call. An empty setting would match every sender, and the account's
    own address is on every inbound email, so either refuses."""
    return RehearsalSenders(
        internal=_safe_sender(
            _SenderSetting("REHEARSAL_INTERNAL_SENDER", settings.rehearsal_internal_sender),
            account=settings.gmail_address,
        ),
        external=_safe_sender(
            _SenderSetting("REHEARSAL_EXTERNAL_SENDER", settings.rehearsal_external_sender),
            account=settings.gmail_address,
        ),
    )


def reset_test(deps: ResetDeps, *, apply: bool) -> int:
    refuse_while_a_copy_may_run(deps)
    plan = find_rehearsal_leftovers(deps)
    for line in plan_lines(plan, applying=apply):
        deps.show(line)
    if apply and not plan.is_empty:
        apply_reset(plan, deps)
    return 0


def find_rehearsal_leftovers(deps: ResetDeps) -> ResetPlan:
    items = [mail_item_of(raw, deps.account) for raw in deps.mailbox.every_message()]
    senders = _lowercased(deps.senders)
    matches = [m for m in rehearsal_mail(items, senders) if TRASH not in m.item.facts.label_ids]
    matched_ids = {match.item.message_id for match in matches}
    return ResetPlan(
        trash_messages=_by_date([m for m in matches if DRAFT not in m.item.facts.label_ids]),
        cancel_events=tuple(_rehearsal_events(deps.calendar, rehearsal_sources(items, senders))),
        drafts_left_alone=_by_date([m for m in matches if DRAFT in m.item.facts.label_ids]),
        left_alone=_tallies(
            [item for item in items if item.message_id not in matched_ids],
            _TallyRules(deps.mailbox.label_ids_by_name().get(PROCESSED), senders),
        ),
    )


def apply_reset(plan: ResetPlan, deps: ResetDeps) -> None:
    """Reads the lock again first: a copy may have started while the plan was read and shown."""
    refuse_while_a_copy_may_run(deps)
    trashed = _write_each(
        [match.item.message_id for match in plan.trash_messages],
        _Write(deps.mailbox.trash, "moved to Trash", deps.show),
    )
    cancelled = _write_each(
        [planned.entry.id for planned in plan.cancel_events],
        _Write(deps.calendar.cancel, "cancelled", deps.show),
    )
    summary = (
        f"{len(trashed.done)} of {len(plan.trash_messages)} messages moved to Trash, "
        f"{len(cancelled.done)} of {len(plan.cancel_events)} events cancelled"
    )
    failed = trashed.failed + cancelled.failed
    if failed:
        raise ResetIncomplete(
            f"Not everything was reset: {summary}; {len(failed)} not done: " + "; ".join(failed),
            next_action="Run make reset-test again: it plans only what is left",
        )
    deps.show(f"Done: {summary}.")


def refuse_while_a_copy_may_run(deps: ResetDeps) -> None:
    try:
        readings = deps.mailbox.lock_readings()
    except AecaError as error:
        raise ResetRefused(
            "reset-test could not read the AECA lock drafts, so it cannot tell whether a copy of "
            f"AECA is running: {error}",
            next_action=f"{STOP_EVERY_COPY}, then run make reset-test again",
        ) from error
    check = LockCheck(now=deps.clock.now(), window=deps.lock_window)
    held = blocking_lock(readings, check)
    if held is not None:
        raise _refusal_for(held, check)


def derived_event_ids(sources: Iterable[str]) -> list[str]:
    """Every id AECA's executor could have created an event under for these sources."""
    return [
        event_id(source, attempt) for source in sources for attempt in range(MAX_EVENT_ATTEMPTS)
    ]


# --- the sender settings ---


@dataclass(frozen=True)
class _SenderSetting:
    name: str
    value: str | None


def _safe_sender(setting: _SenderSetting, *, account: str) -> str:
    if setting.value is None:
        raise _unsafe_sender(setting, "is not set")
    if not SINGLE_ADDRESS.fullmatch(setting.value):
        raise _unsafe_sender(setting, "is not exactly one email address")
    if same_gmail_account(setting.value, account):
        raise _unsafe_sender(setting, "is the account's own address (GMAIL_ADDRESS)")
    return setting.value


def _unsafe_sender(setting: _SenderSetting, problem: str) -> ConfigError:
    return ConfigError(
        f"make reset-test refuses: {setting.name} {problem}, so its rules could match mail the "
        "rehearsal did not send.",
        next_action=f"Set {setting.name} in .env to the address the rehearsal sent from, "
        "then run again",
    )


def _lowercased(senders: RehearsalSenders) -> frozenset[str]:
    return frozenset({senders.internal.strip().lower(), senders.external.strip().lower()})


# --- the events ---


def _rehearsal_events(calendar: ResetCalendar, sources: Sequence[str]) -> list[PlannedEvent]:
    seeded = [
        PlannedEvent(entry, LeftoverRule.SEEDED_EVENT)
        for entry in calendar.tagged_events(SEED_TAG_NAME, SEED_TAG_VALUE)
        if entry.private_properties.get(SEED_TAG_NAME) == SEED_TAG_VALUE and not entry.cancelled
    ]
    derived = [
        PlannedEvent(entry, LeftoverRule.DERIVED_EVENT)
        for candidate in derived_event_ids(sources)
        if (entry := calendar.event(candidate)) is not None and not entry.cancelled
    ]
    unique: dict[str, PlannedEvent] = {}
    for planned in seeded + derived:
        unique.setdefault(planned.entry.id, planned)
    return sorted(unique.values(), key=lambda planned: (planned.entry.starts, planned.entry.id))


# --- what is left alone ---


@dataclass(frozen=True)
class _TallyRules:
    processed_label_id: str | None
    rehearsal_senders: frozenset[str]

    def unprocessed_in_inbox(self, item: MailItem) -> bool:
        labels = item.facts.label_ids
        return INBOX in labels and (
            self.processed_label_id is None or self.processed_label_id not in labels
        )


def _tallies(left_alone: Sequence[MailItem], rules: _TallyRules) -> tuple[SenderTally, ...]:
    by_sender: dict[str, list[MailItem]] = defaultdict(list)
    for item in left_alone:
        if TRASH not in item.facts.label_ids:
            by_sender[item.facts.sender].append(item)
    tallies = [
        SenderTally(
            sender=sender,
            messages=len(group),
            unprocessed_in_inbox=sum(rules.unprocessed_in_inbox(item) for item in group),
            rehearsal_address=sender in rules.rehearsal_senders,
        )
        for sender, group in by_sender.items()
    ]
    return tuple(sorted(tallies, key=lambda tally: (-tally.messages, tally.sender)))


# --- applying ---


@dataclass(frozen=True)
class _Write:
    perform: Callable[[str], None]
    done_as: str
    show: Callable[[str], None]


@dataclass
class _Outcome:
    done: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)


# One failure must not stop the rest: every item is independent, and the summary names each id that
# failed, so a rerun can finish the job.
def _write_each(item_ids: Sequence[str], write: _Write) -> _Outcome:
    outcome = _Outcome()
    for item_id in item_ids:
        try:
            write.perform(item_id)
        except AecaError as error:
            outcome.failed.append(f"{item_id} ({error})")
            write.show(f"  not done: {item_id}")
            continue
        outcome.done.append(item_id)
        write.show(f"  {write.done_as}: {item_id}")
    return outcome


def _by_date(matches: list[MailMatch]) -> tuple[MailMatch, ...]:
    return tuple(sorted(matches, key=lambda m: (m.item.facts.received_at, m.item.message_id)))


# --- refusing ---


def _refusal_for(held: LockReading, check: LockCheck) -> ResetRefused:
    window = int(check.window.total_seconds())
    if held.heartbeat is None:
        return ResetRefused(
            f'The lock draft {held.draft_id} ("AECA lock") has no readable heartbeat, so a copy '
            "of AECA may be running.",
            next_action=f'{STOP_EVERY_COPY}, check the "AECA lock" draft in Drafts, then run again',
        )
    age = int((check.now - held.heartbeat).total_seconds())
    if age < 0:
        return ResetRefused(
            f"A copy of AECA may be running: the lock's heartbeat is {-age} s in the future, so a "
            "clock is wrong somewhere.",
            next_action=f"{STOP_EVERY_COPY} and check this machine's clock, then run again",
        )
    return ResetRefused(
        f"A copy of AECA may be running: the lock's heartbeat is {age} s old, under the "
        f"{window} s limit.",
        next_action=f"Stop that copy with make stop, then retry in {window - age} s",
    )
