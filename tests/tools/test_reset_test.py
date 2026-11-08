"""reset-test cleans a rehearsal out of the account handed to the evaluators, and nothing else.

The cases are the tech-lead's (task-17-tech-lead-cases.md), with the controller's rulings
(task-17-context.md). Each test builds its own account; the made_by_rehearsal flag on every
fixture item is the oracle, never reset-test's own finder.
"""

import ast
import inspect
import re
from collections.abc import Callable
from dataclasses import fields
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from fakes.fake_gmail_account import SNIPPET_PREFIX
from fakes.fixed_clock import FixedClock
from fakes.google_stub import http_error
from fakes.write_journal import STDOUT
from support.rehearsal_account import (
    ACCOUNT,
    ALREADY_HANDLED_EVENTS,
    ALREADY_HANDLED_MESSAGES,
    BODY_PREFIX,
    E_EXT2_1,
    E_MAX,
    E_MTG,
    EV_EVENT,
    EV_INVITE,
    GRADER,
    LOCK_SUBJECT,
    LOOKALIKE_EVENT,
    MANUAL_EVENT,
    NOW,
    R_MTG,
    REHEARSAL_APPROVAL_THREADS,
    S_EXT,
    S_INT,
    RehearsalAccount,
    events_to_cancel,
    every_fixture_id,
    messages_to_trash,
    rehearsal_account,
    run_reset,
    untouched_events,
    untouched_messages,
)
from test_architecture import SOURCE_ROOT, imported_modules

from aeca import cli
from aeca.adapters.google_reset import GmailResetMailbox, GoogleResetCalendar
from aeca.application import executor
from aeca.tools import reset_test as reset_test_module
from aeca.tools import tool_commands
from aeca.tools.reset_test import ResetDeps

TRASH, DRAFT = "TRASH", "DRAFT"
Arrange = Callable[[RehearsalAccount], object]
LOCK_WINDOW_SECONDS = 600


def tokens(text: str) -> set[str]:
    return set(re.findall(r"[\w-]+", text))


def line_naming(stdout: str, item_id: str) -> str:
    [line] = [line for line in stdout.splitlines() if item_id in tokens(line)]
    return line


def gmail_methods(account: RehearsalAccount) -> set[str]:
    return {method for _, method, _ in account.journal.mutations_of("gmail")}


def calendar_methods(account: RehearsalAccount) -> set[str]:
    return {method for _, method, _ in account.journal.mutations_of("calendar")}


def live_instances(account: RehearsalAccount) -> list[dict[str, object]]:
    items: list[dict[str, object]] = []
    page_token = None
    while True:
        reply = (
            account.calendar.events()
            .list(
                calendarId="primary",
                singleEvents=True,
                timeMin="2026-11-01T00:00:00+00:00",
                timeMax="2027-01-31T00:00:00+00:00",
                pageToken=page_token,
            )
            .execute()
        )
        items.extend(reply["items"])
        page_token = reply.get("nextPageToken")
        if not page_token:
            return items


def rehearsal_event_ids(account: RehearsalAccount) -> set[str]:
    return {event for event, made in account.calendar.made_by_rehearsal.items() if made}


def lock_draft_bytes(account: RehearsalAccount) -> dict[str, bytes]:
    return {i: raw for i, raw in account.drafts.stored.items() if LOCK_SUBJECT.encode() in raw}


# --- B1. A dry run is read-only, and --yes prints before it writes ---


def test_b1_a_dry_run_writes_nothing_and_names_exactly_the_planned_ids() -> None:
    account = rehearsal_account()
    planned = messages_to_trash(account) | events_to_cancel(account)

    run = run_reset(account, apply=False)

    printed = tokens(account.journal.stdout())
    assert run.code == 0
    assert account.journal.mutations() == []
    assert planned <= printed
    assert (every_fixture_id(account) - planned) & printed == set()


def test_b1_yes_prints_the_whole_plan_before_its_first_write() -> None:
    dry = rehearsal_account()
    run_reset(dry, apply=False)
    plan_lines = [line for line in dry.journal.stdout().splitlines() if line.strip()]
    account = rehearsal_account()

    run_reset(account, apply=True)

    entries = account.journal.entries
    first_write = next(i for i, entry in enumerate(entries) if entry[0] != STDOUT)
    printed_before_writing = [entry[2] for entry in entries[:first_write]]
    for line in plan_lines[1:]:
        assert line in printed_before_writing


# --- B2. Only Trash and cancel; nothing is sent, deleted or relabelled ---


def test_b2_only_messages_trash_and_events_delete_are_ever_written() -> None:
    account = rehearsal_account()
    labels_before = {i: frozenset(m.labels) for i, m in account.gmail.stored.items()}

    run_reset(account, apply=True)

    trashed = account.trashed_ids()
    assert gmail_methods(account) == {"messages.trash"}
    assert len(trashed) == len(set(trashed))
    assert not any(labels_before[i] & {DRAFT, TRASH} for i in trashed)
    assert calendar_methods(account) == {"events.delete"}
    assert account.gmail.draft_writes() == []


# --- B3. Exactly what the rehearsal made, and nothing else, with no lock at all ---


def test_b3_exactly_the_rehearsal_mail_is_trashed_and_everything_else_is_unchanged() -> None:
    account = rehearsal_account(lock_age=None)
    expected = messages_to_trash(account)
    untouched_before = untouched_messages(account)

    run = run_reset(account, apply=True)

    assert run.code == 0
    assert set(account.trashed_ids()) == expected
    assert untouched_messages(account) == untouched_before


def test_b3a_a_neighbour_in_a_shared_thread_is_left_alone() -> None:
    account = rehearsal_account(lock_age=None)

    run_reset(account, apply=True)

    trashed = set(account.trashed_ids())
    assert {"r-int", "r-ack"} <= trashed
    assert not {"ev-thread", "ev-reply"} & trashed
    assert not any(method.startswith("threads.") for method in gmail_methods(account))


def test_b3b_only_the_exact_address_counts_as_a_rehearsal_sender() -> None:
    account = rehearsal_account(lock_age=None)

    run_reset(account, apply=True)

    trashed = set(account.trashed_ids())
    lookalikes = {
        "ev-display",
        "ev-multi",
        "ev-multi-first",
        *(f"ev-look-{n}" for n in range(1, 6)),
    }
    assert not lookalikes & trashed
    assert "r-ext-arch" in trashed


def test_b3c_recipients_and_mentions_never_count_for_inbound_mail() -> None:
    account = rehearsal_account(lock_age=None)

    run_reset(account, apply=True)

    inbound = {"ev-thread", "ev-to-both", "ev-replyto", "ev-self"}
    assert not inbound & set(account.trashed_ids())


def test_b3c_the_accounts_own_mail_to_a_rehearsal_sender_and_someone_else_is_left_alone() -> None:
    account = rehearsal_account(lock_age=None)

    run_reset(account, apply=True)

    assert "ev-mixed" not in account.trashed_ids()


def test_b3d_forged_aeca_headers_on_inbound_mail_are_ignored() -> None:
    account = rehearsal_account(lock_age=None)

    run_reset(account, apply=True)

    assert "ev-forged" not in account.trashed_ids()
    assert "r-req" in account.trashed_ids()


def test_b3e_the_evaluators_own_copy_and_self_sent_mail_are_left_alone() -> None:
    account = rehearsal_account(lock_age=None)

    run_reset(account, apply=True)

    trashed = set(account.trashed_ids())
    assert not {"ev-self", "ev-req", "ev-approve", "ev-reply"} & trashed
    assert {"r-req1-self", "r-self-approve"} <= trashed


@pytest.mark.parametrize("shared_thread", REHEARSAL_APPROVAL_THREADS)
def test_i1_the_evaluators_request_and_hand_approve_in_a_rehearsal_thread_are_left_alone(
    shared_thread: str,
) -> None:
    account = rehearsal_account(lock_age=None, evaluators_approval_thread=shared_thread)
    expected = messages_to_trash(account)

    run_reset(account, apply=True)

    trashed = set(account.trashed_ids())
    assert not {"ev-req", "ev-approve"} & trashed
    assert "r-self-approve" in trashed
    assert trashed == expected


def test_i1_a_hand_reply_in_a_rehearsal_thread_to_someone_else_is_left_alone() -> None:
    account = rehearsal_account(lock_age=None)

    run_reset(account, apply=True)

    assert "ev-hand-reply" not in account.trashed_ids()
    assert "r-req" in account.trashed_ids()


def test_b3f_mail_from_google_is_left_alone_labels_included() -> None:
    account = rehearsal_account(lock_age=None)
    google = {i: frozenset(account.labels_of(i)) for i in ("g-1", "g-2", "g-3", "g-4")}

    run_reset(account, apply=True)

    assert not set(google) & set(account.trashed_ids())
    assert {i: frozenset(account.labels_of(i)) for i in google} == google


# --- B4. Events: exactly the seeded and derived ones, the whole series, wherever they fall ---


def test_b4_exactly_the_seeded_and_derived_events_are_cancelled() -> None:
    account = rehearsal_account()
    expected = events_to_cancel(account)
    assert len(expected) == 21

    run_reset(account, apply=True)

    cancelled = account.cancelled_ids()
    assert sorted(cancelled) == sorted(expected)
    assert {E_MTG, E_EXT2_1, E_MAX} <= set(cancelled)
    never = {*ALREADY_HANDLED_EVENTS, EV_EVENT, EV_INVITE, MANUAL_EVENT, LOOKALIKE_EVENT}
    assert not never & {target for _, _, target in account.journal.mutations_of("calendar")}


def test_b4_a_recurring_seed_is_cancelled_as_its_master_and_no_instance_survives() -> None:
    account = rehearsal_account()
    weekly_before = [i for i in live_instances(account) if "Weekly sync" in str(i["summary"])]
    assert len(weekly_before) == 4

    run_reset(account, apply=True)

    assert not any("_" in cancelled for cancelled in account.cancelled_ids())
    rehearsal = rehearsal_event_ids(account)
    leftovers = [
        item
        for item in live_instances(account)
        if item["id"] in rehearsal or item.get("recurringEventId") in rehearsal
    ]
    assert leftovers == []


# --- B5. Refuse while any copy may hold the lock, and fail closed ---


def lock_aged(age: timedelta) -> Arrange:
    return lambda account: account.add_lock(heartbeat=NOW - age)


def lock_body(body: str) -> Arrange:
    return lambda account: account.add_lock(heartbeat=None, body=body)


def two_locks(account: RehearsalAccount) -> None:
    account.add_lock(heartbeat=NOW - timedelta(hours=1))
    account.add_lock(heartbeat=NOW - timedelta(seconds=10))


def three_locks_with_the_fresh_one_in_the_middle(account: RehearsalAccount) -> None:
    account.add_lock(heartbeat=NOW - timedelta(hours=1))
    account.add_lock(heartbeat=NOW - timedelta(seconds=10))
    account.add_lock(heartbeat=NOW - timedelta(hours=2))


def drafts_list_fails(error: Exception) -> Arrange:
    def fail(account: RehearsalAccount) -> None:
        account.add_lock(heartbeat=NOW - timedelta(hours=1))
        account.drafts.list_failure = error

    return fail


def no_lock(_account: RehearsalAccount) -> None:
    return None


REFUSING_LOCKS = [
    pytest.param(lock_aged(timedelta(0)), "0 s old", id="age-0s"),
    pytest.param(lock_aged(timedelta(seconds=89)), "89 s old", id="age-89s"),
    pytest.param(lock_aged(timedelta(seconds=599)), "599 s old", id="age-599s"),
    pytest.param(lock_aged(-timedelta(minutes=10)), "in the future", id="10-min-in-the-future"),
    pytest.param(lock_body("copy_id: theirs\n"), "no readable heartbeat", id="no-heartbeat-line"),
    pytest.param(
        lock_body("copy_id: theirs\nheartbeat: 2026-11-08T17:59:00\n"),
        "no readable heartbeat",
        id="naive-heartbeat",
    ),
    pytest.param(two_locks, "10 s old", id="lowest-stale-other-fresh"),
    pytest.param(
        three_locks_with_the_fresh_one_in_the_middle, "10 s old", id="fresh-lock-in-the-middle"
    ),
    pytest.param(drafts_list_fails(http_error(500)), "could not read", id="drafts-list-500"),
    pytest.param(
        drafts_list_fails(ConnectionResetError("reset by peer")),
        "could not read",
        id="drafts-list-network-error",
    ),
]


@pytest.mark.parametrize(("arrange", "says"), REFUSING_LOCKS)
def test_b5_refuses_with_no_write_while_a_copy_may_hold_the_lock(
    arrange: Arrange, says: str
) -> None:
    account = rehearsal_account(lock_age=None)
    arrange(account)
    locks_before = lock_draft_bytes(account)

    run = run_reset(account, apply=True)

    assert run.code != 0
    assert account.journal.mutations() == []
    assert says in run.error
    assert "make stop" in run.error
    assert lock_draft_bytes(account) == locks_before
    assert account.gmail.draft_writes() == []


@pytest.mark.parametrize(
    "age", [timedelta(seconds=89), timedelta(seconds=599)], ids=["89s", "599s"]
)
def test_b5_a_fresh_lock_says_how_long_to_wait(age: timedelta) -> None:
    account = rehearsal_account(lock_age=None)
    account.add_lock(heartbeat=NOW - age)

    run = run_reset(account, apply=True)

    wait = LOCK_WINDOW_SECONDS - int(age.total_seconds())
    assert f"retry in {wait} s" in run.error


@pytest.mark.parametrize(
    "arrange",
    [pytest.param(lock_aged(timedelta(seconds=601)), id="age-601s"), pytest.param(no_lock)],
)
def test_b5_proceeds_once_the_lock_is_stale_or_absent(arrange: Arrange) -> None:
    account = rehearsal_account(lock_age=None)
    arrange(account)
    locks_before = lock_draft_bytes(account)
    expected = messages_to_trash(account)

    run = run_reset(account, apply=True)

    assert run.code == 0
    assert set(account.trashed_ids()) == expected
    assert lock_draft_bytes(account) == locks_before
    assert account.gmail.draft_writes() == []


def test_b5_a_dry_run_refuses_too_while_the_lock_is_fresh() -> None:
    account = rehearsal_account(lock_age=timedelta(seconds=30))

    run = run_reset(account, apply=False)

    assert run.code != 0 and account.journal.mutations() == []


# --- B5 row 8 and B6, through the command itself ---


@pytest.fixture
def reset_command(
    monkeypatch: pytest.MonkeyPatch, minimal_run_env: dict[str, str]
) -> pytest.MonkeyPatch:
    environment = minimal_run_env | {
        "GMAIL_ADDRESS": ACCOUNT,
        "REHEARSAL_INTERNAL_SENDER": S_INT,
        "REHEARSAL_EXTERNAL_SENDER": S_EXT,
    }
    for name, value in environment.items():
        monkeypatch.setenv(name, value)
    for name in ("AECA_DATA_DIR", "FORCE_LOCK", "POLL_SECONDS"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(tool_commands, "SystemClock", lambda: FixedClock(NOW))
    return monkeypatch


def google_over(monkeypatch: pytest.MonkeyPatch, account: RehearsalAccount) -> list[object]:
    """Hands the command the fakes instead of Google, and records each time it asked."""
    built: list[object] = []

    def build(settings: object) -> SimpleNamespace:
        built.append(settings)
        return SimpleNamespace(
            reset_mailbox=GmailResetMailbox(account.gmail),
            reset_calendar=GoogleResetCalendar(account.calendar),
        )

    monkeypatch.setattr(tool_commands, "build_google_services", build)
    return built


def test_b5_force_lock_does_not_override_a_fresh_lock(
    reset_command: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    account = rehearsal_account(lock_age=timedelta(seconds=30))
    google_over(reset_command, account)
    reset_command.setenv("FORCE_LOCK", "true")

    code = cli.main(["reset-test", "--yes"])

    err = capsys.readouterr().err
    assert code == 1
    assert "30 s old" in err and "make stop" in err
    assert account.journal.mutations() == [] and account.gmail.draft_writes() == []


UNSAFE_SENDER_VALUES = [
    pytest.param(None, id="unset"),
    pytest.param("", id="empty"),
    pytest.param("   ", id="blank"),
    pytest.param("ionel.cimpan", id="local-part-only"),
    pytest.param("@protonmail.com", id="domain-with-at"),
    pytest.param("protonmail.com", id="domain-only"),
    pytest.param("a@x.example, b@y.example", id="two-addresses"),
    pytest.param(ACCOUNT, id="the-account"),
    pytest.param("AECA.Test74@gmail.com", id="the-account-with-case-and-dots"),
]


@pytest.mark.parametrize("setting", ["REHEARSAL_INTERNAL_SENDER", "REHEARSAL_EXTERNAL_SENDER"])
@pytest.mark.parametrize("value", UNSAFE_SENDER_VALUES)
def test_b6_a_sender_setting_that_could_match_too_much_refuses_before_any_google_call(
    reset_command: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    setting: str,
    value: str | None,
) -> None:
    account = rehearsal_account(lock_age=None)
    built = google_over(reset_command, account)
    if value is None:
        reset_command.delenv(setting)
    else:
        reset_command.setenv(setting, value)

    code = cli.main(["reset-test", "--yes"])

    assert code == 1
    assert setting in capsys.readouterr().err
    assert built == []
    assert account.journal.mutations() == []


# --- B7. An interrupted run converges, and finds work derived from sources already in Trash ---


def test_b7_an_interrupted_run_converges_on_a_rerun() -> None:
    account = rehearsal_account()
    for source in (R_MTG, "r-ext2"):
        account.labels_of(source).add(TRASH)
    untouched_before = untouched_messages(account), untouched_events(account)

    run_reset(account, apply=False)

    dry = account.journal.stdout()
    assert "R3" in line_naming(dry, "r-req1-self")
    assert "R4" in line_naming(dry, "r-self-approve")
    assert "E2" in line_naming(dry, E_MTG) and "E2" in line_naming(dry, E_EXT2_1)

    account.journal.clear()
    account.gmail.failures.on_call[("messages.trash", 3)] = http_error(500)
    interrupted = run_reset(account, apply=True)

    assert interrupted.code != 0
    assert "13 of 14 messages" in interrupted.error and "1 not done" in interrupted.error

    account.journal.clear()
    final = run_reset(account, apply=True)

    assert final.code == 0
    assert_the_oracle_holds(account)
    assert (untouched_messages(account), untouched_events(account)) == untouched_before


def assert_the_oracle_holds(account: RehearsalAccount) -> None:
    for message in account.gmail.stored.values():
        if message.made_by_rehearsal and DRAFT not in message.labels:
            assert TRASH in message.labels, message.id
    for event_id in rehearsal_event_ids(account):
        assert account.calendar.status_of(event_id) == "cancelled", event_id


# --- B8. The second dry run is empty, and that is true by the oracle ---


def test_b8_after_yes_a_second_dry_run_is_empty_and_the_oracle_agrees() -> None:
    account = rehearsal_account()
    run_reset(account, apply=True)
    applied = account.journal.stdout()
    account.journal.clear()

    second = run_reset(account, apply=False)

    again = account.journal.stdout()
    assert second.code == 0 and account.journal.mutations() == []
    assert "Nothing to reset" in again
    assert TRASH in account.labels_of("r-spam")
    assert_the_oracle_holds(account)
    handled = ALREADY_HANDLED_MESSAGES | ALREADY_HANDLED_EVENTS
    assert not handled & (tokens(applied) | tokens(again))


# --- B9. Derived ids use the Gmail API id and AECA's own attempt bounds ---


def test_b9_derived_work_is_found_from_the_gmail_id_up_to_the_last_attempt() -> None:
    account = rehearsal_account()

    run_reset(account, apply=False)

    printed = tokens(account.journal.stdout())
    assert {E_MTG, E_MAX, "r-req1-self"} <= printed


def test_b9_the_event_attempt_bound_is_the_executors_constant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = ast.parse(inspect.getsource(reset_test_module))
    imported = {
        alias.name
        for node in ast.walk(source)
        if isinstance(node, ast.ImportFrom) and node.module == "aeca.application.executor"
        for alias in node.names
    }
    assert "MAX_EVENT_ATTEMPTS" in imported
    monkeypatch.setattr(reset_test_module, "MAX_EVENT_ATTEMPTS", executor.MAX_EVENT_ATTEMPTS - 1)
    account = rehearsal_account()

    run_reset(account, apply=False)

    assert E_MAX not in tokens(account.journal.stdout())


# --- B10. No dependency on the state store ---


def test_b10_reset_deps_hold_no_state_journal_or_data_dir() -> None:
    names = {field.name for field in fields(ResetDeps)}

    assert names == {"mailbox", "calendar", "account", "senders", "clock", "lock_window", "show"}


def test_b10_the_command_creates_nothing_in_the_data_dir(
    reset_command: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    missing = tmp_path / "no-such-dir"
    reset_command.setenv("AECA_DATA_DIR", str(missing))
    reset_command.chdir(tmp_path)
    account = rehearsal_account()
    planned = messages_to_trash(account) | events_to_cancel(account)
    google_over(reset_command, account)

    dry_code = cli.main(["reset-test"])
    dry = capsys.readouterr().out
    yes_code = cli.main(["reset-test", "--yes"])

    assert (dry_code, yes_code) == (0, 0)
    assert planned <= tokens(dry)
    assert (every_fixture_id(account) - planned) & tokens(dry) == set()
    assert not missing.exists() and not (tmp_path / "data").exists()


# --- N1 to N8 ---


def test_n1_every_listing_is_followed_to_its_last_page() -> None:
    account = rehearsal_account(lock_age=None)
    assert account.gmail.page_size == account.calendar.page_size == account.drafts.page_size == 2
    account.add_lock(heartbeat=NOW - timedelta(seconds=5))

    refused = run_reset(account, apply=False)

    assert refused.code != 0, "the lock is on the last page of drafts"


def test_n2_each_planned_item_shows_what_it_is_and_why() -> None:
    account = rehearsal_account()

    run_reset(account, apply=False)

    stdout = account.journal.stdout()
    budget = line_naming(stdout, "r-int")
    for shown in ("2026-11-07 09:00", S_INT, ACCOUNT, "Quick question about the Q4 budget"):
        assert shown in budget
    assert "Inbox" in budget and "R1" in budget
    assert "Spam" in line_naming(stdout, "r-spam")
    assert "Archived" in line_naming(stdout, "r-ext-arch")
    assert "Sent" in line_naming(stdout, "r-req")
    meeting = line_naming(stdout, E_MTG)
    assert "2026-11-11" in meeting and "E2" in meeting and "Meeting: Can we meet" in meeting


def test_n2_the_plan_totals_each_rule() -> None:
    account = rehearsal_account()

    run_reset(account, apply=False)

    [totals] = [line for line in account.journal.stdout().splitlines() if "Totals" in line]
    for count in ("R1 8", "R2 5", "R3 2", "R4 1", "E1 18", "E2 3"):
        assert count in totals


def test_n2_what_is_left_alone_is_summed_up_by_sender_and_flags_unprocessed_inbox_mail() -> None:
    account = rehearsal_account()

    run_reset(account, apply=False)

    left_alone = left_alone_lines(account.journal.stdout())
    grader = sender_line(left_alone, GRADER)
    assert "6 messages" in grader and "2 in the Inbox without AECA/processed" in grader
    assert "rehearsal address" in sender_line(left_alone, S_EXT)
    assert "rehearsal address" not in sender_line(left_alone, "ionel.cimpan+aeca@protonmail.com")


def sender_line(lines: list[str], sender: str) -> str:
    [line] = [line for line in lines if line.strip().startswith(f"{sender}:")]
    return line


def left_alone_lines(stdout: str) -> list[str]:
    lines = stdout.splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith("Left alone"))
    return lines[start + 1 :]


def test_n2_no_body_or_snippet_is_ever_printed() -> None:
    account = rehearsal_account()

    run_reset(account, apply=True)

    stdout = account.journal.stdout()
    assert BODY_PREFIX not in stdout and SNIPPET_PREFIX not in stdout


def test_n3_an_orphan_draft_is_listed_and_left_alone_and_the_lock_never_shows() -> None:
    account = rehearsal_account(with_orphan_draft=True)

    run_reset(account, apply=False)
    dry = account.journal.stdout()
    run_reset(account, apply=True)

    assert "r-orphan-draft" in tokens(dry)
    assert "Drafts" in dry
    assert "r-orphan-draft" not in account.trashed_ids()
    assert "d-lock" not in tokens(dry)


def test_n4_the_lock_is_read_again_just_before_the_first_write() -> None:
    account = rehearsal_account(lock_age=None)
    lock_id = account.add_lock(heartbeat=NOW - timedelta(hours=1))
    reads: list[int] = []

    def a_copy_starts_after_planning(drafts: object) -> None:
        reads.append(1)
        if len(reads) == 2:
            fresh = f"copy_id: late\nheartbeat: {(NOW - timedelta(seconds=5)).isoformat()}\n"
            account.drafts.add(subject=LOCK_SUBJECT, body=fresh, draft_id=lock_id)

    account.drafts.before_list = a_copy_starts_after_planning

    run = run_reset(account, apply=True)

    assert len(reads) == 2
    assert run.code != 0 and account.journal.mutations() == []


def test_n5_one_failing_event_leaves_the_others_cancelled_and_is_named() -> None:
    account = rehearsal_account()
    expected = events_to_cancel(account)
    failing = sorted(expected)[0]
    account.calendar.failures.on_target[("events.delete", failing)] = http_error(403)

    run = run_reset(account, apply=True)

    assert run.code != 0 and failing in run.error
    assert all(account.calendar.status_of(e) == "cancelled" for e in expected - {failing})
    assert set(account.trashed_ids()) == messages_to_trash(rehearsal_account())


def test_n6_a_second_yes_on_a_clean_account_writes_nothing() -> None:
    account = rehearsal_account()
    run_reset(account, apply=True)
    account.journal.clear()

    again = run_reset(account, apply=True)

    assert again.code == 0 and account.journal.mutations() == []


def test_n7_an_approval_well_past_the_first_attempts_is_found() -> None:
    account = rehearsal_account()

    run_reset(account, apply=False)

    assert "R3" in line_naming(account.journal.stdout(), "r-req6")


def test_n8_mail_from_a_rehearsal_address_without_a_dmarc_pass_for_it_is_left_alone() -> None:
    account = rehearsal_account()

    run_reset(account, apply=True)

    assert not {"ev-spoof", "ev-spoof-domain"} & set(account.trashed_ids())


# --- the command ---


def test_the_command_prints_the_plan_on_stdout_and_stops_without_yes(
    reset_command: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    account = rehearsal_account()
    google_over(reset_command, account)

    code = cli.main(["reset-test"])

    out = capsys.readouterr().out
    assert code == 0 and "r-int" in tokens(out) and "--yes" in out
    assert account.journal.mutations() == []


# --- the layers: the tool reads Google only through its adapter, and names each value once ---


def module_imports(relative: str) -> set[str]:
    path = SOURCE_ROOT / "aeca" / relative
    package = ".".join(path.relative_to(SOURCE_ROOT).parent.parts)
    return imported_modules(path.read_text(encoding="utf-8"), package=package)


@pytest.mark.parametrize("module", ["tools/reset_test.py", "tools/reset_plan.py"])
def test_the_tool_and_its_plan_never_import_an_adapter(module: str) -> None:
    assert not {name for name in module_imports(module) if name.startswith("aeca.adapters")}


def modules_assigning(name: str) -> list[str]:
    found = []
    for path in sorted((SOURCE_ROOT / "aeca").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        targets = [
            target
            for node in ast.walk(tree)
            if isinstance(node, ast.Assign)
            for target in node.targets
        ]
        if any(isinstance(target, ast.Name) and target.id == name for target in targets):
            found.append(str(path.relative_to(SOURCE_ROOT / "aeca")))
    return found


def test_google_calendar_names_the_cancelled_status_once() -> None:
    assert modules_assigning("CANCELLED") == ["adapters/google_calendar.py"]


def test_the_trash_label_is_named_once_beside_the_other_system_labels() -> None:
    assert modules_assigning("TRASH") == ["domain/labels.py"]
