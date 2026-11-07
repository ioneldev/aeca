"""The handover account as the controller measured it after a rehearsal, plus the evaluators' mail,
as the tech-lead's cases for reset-test describe it (task-17-tech-lead-cases.md §2).

Every message and event carries a test-only made_by_rehearsal flag. That flag is the oracle: the
tests check reset-test against it, never against reset-test's own finder.
"""

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from email.message import EmailMessage
from typing import Any
from zoneinfo import ZoneInfo

from fakes.fake_calendar_service import FakeCalendarService
from fakes.fake_drafts_service import FakeDraftsService
from fakes.fake_gmail_account import FakeGmailAccount, StoredMessage
from fakes.fixed_clock import FixedClock
from fakes.write_journal import WriteJournal
from support.emails import make_raw

from aeca.adapters.google_reset import GmailResetMailbox, GoogleResetCalendar
from aeca.adapters.google_seed_calendar import GoogleSeedCalendar
from aeca.application.executor import MAX_EVENT_ATTEMPTS
from aeca.domain.errors import AecaError
from aeca.domain.ids import action_key, alert_id, approval_id, event_id
from aeca.domain.reset_lock import reset_lock_window
from aeca.settings import RehearsalSenders
from aeca.tools.reset_test import ResetDeps, reset_test
from aeca.tools.seed_calendar import SeedWeek, seed_calendar

ACCOUNT = "aecatest74@gmail.com"
S_INT = "ionel.cimpan@icloud.com"
S_EXT = "ionel.cimpan@protonmail.com"
GRADER = "grader@telekom.example"
NOW = datetime(2026, 11, 8, 18, 0, tzinfo=UTC)
BUCHAREST = ZoneInfo("Europe/Bucharest")
SEEDED_WEEK = date(2026, 11, 9)
SECOND_SEEDED_WEEK = date(2026, 11, 16)
POLL_SECONDS = 30

PROCESSED_LABEL_ID = "Label_1"
URGENT_LABEL_ID = "Label_2"
USER_LABELS = {"AECA/processed": PROCESSED_LABEL_ID, "URGENT": URGENT_LABEL_ID}
INBOX, SENT, SPAM, TRASH, DRAFT = "INBOX", "SENT", "SPAM", "TRASH", "DRAFT"
PROCESSED_INBOX = {INBOX, PROCESSED_LABEL_ID}
UPDATES = "CATEGORY_UPDATES"

R_MTG = "18f0c0ffee000001"
LOCK_SUBJECT = "AECA lock"
OTHER_COPY = "evaluators-copy"
BODY_PREFIX = "BODY-OF-"
EVALUATORS_OWN_APPROVAL_THREAD = "t-ev-appr"
# Rehearsal approval threads the evaluators' own request and hand APPROVE may end up in, when Gmail
# threads them together by subject.
REHEARSAL_APPROVAL_THREADS = ("t-self", "t-req0")

# Already in Trash or already cancelled before reset-test runs: no call is expected on them.
ALREADY_HANDLED_MESSAGES = frozenset({"sp-1", "sp-2"})
EVENT_EXT2_ATTEMPT_0 = event_id("r-ext2", 0)
SPIKE_EVENT = "spikeev0123456789"
ALREADY_HANDLED_EVENTS = frozenset({SPIKE_EVENT, EVENT_EXT2_ATTEMPT_0})

E_MTG = event_id(R_MTG, 0)
E_EXT2_1 = event_id("r-ext2", 1)
E_MAX = event_id("r-q3", MAX_EVENT_ATTEMPTS - 1)
EV_EVENT = event_id("ev-thread", 0)
EV_INVITE = "evinvite0123456789"
MANUAL_EVENT = "manual0987654321"
LOOKALIKE_EVENT = "aeca0123456789abcdefghijklmnop"


@dataclass
class RehearsalAccount:
    journal: WriteJournal
    gmail: FakeGmailAccount
    calendar: FakeCalendarService

    @property
    def drafts(self) -> FakeDraftsService:
        return self.gmail.drafts_service

    def labels_of(self, message_id: str) -> set[str]:
        return self.gmail.stored[message_id].labels

    def trashed_ids(self) -> list[str]:
        return self._targets_of("messages.trash")

    def cancelled_ids(self) -> list[str]:
        return self._targets_of("events.delete")

    def _targets_of(self, method: str) -> list[str]:
        return [target for _, called, target in self.journal.mutations() if called == method]

    def add_lock(self, *, heartbeat: datetime | None, body: str | None = None) -> str:
        default = f"copy_id: {OTHER_COPY}\nheartbeat: {_iso(heartbeat)}\n"
        return self.drafts.add(subject=LOCK_SUBJECT, body=default if body is None else body)


@dataclass(frozen=True)
class ResetRun:
    code: int
    # What the CLI prints on stderr: the error, then what to do next.
    error: str


def rehearsal_account(
    *,
    lock_age: timedelta | None = timedelta(hours=1),
    with_orphan_draft: bool = False,
    evaluators_approval_thread: str = EVALUATORS_OWN_APPROVAL_THREAD,
) -> RehearsalAccount:
    journal = WriteJournal()
    gmail = FakeGmailAccount(journal=journal, user_labels=dict(USER_LABELS))
    calendar = FakeCalendarService(journal=journal)
    account = RehearsalAccount(journal=journal, gmail=gmail, calendar=calendar)
    options = _MailOptions(with_orphan_draft, evaluators_approval_thread)
    for message in _messages(options):
        gmail.add(message)
    _fill_calendar(calendar)
    for number in range(3):
        account.drafts.add(subject=f"my own draft {number}", body="a note to self")
    if lock_age is not None:
        account.add_lock(heartbeat=NOW - lock_age)
    journal.clear()
    return account


def reset_deps(account: RehearsalAccount) -> ResetDeps:
    return ResetDeps(
        mailbox=GmailResetMailbox(account.gmail),
        calendar=GoogleResetCalendar(account.calendar),
        account=ACCOUNT,
        senders=RehearsalSenders(internal=S_INT, external=S_EXT),
        clock=FixedClock(NOW),
        lock_window=reset_lock_window(POLL_SECONDS),
        show=account.journal.say,
    )


def run_reset(account: RehearsalAccount, *, apply: bool) -> ResetRun:
    """As the CLI runs it: an AecaError becomes exit 1 and its text on stderr."""
    try:
        code = reset_test(reset_deps(account), apply=apply)
    except AecaError as error:
        return ResetRun(code=1, error=f"{error}\n{error.next_action}")
    return ResetRun(code=code, error="")


# --- the oracle ---


def messages_to_trash(account: RehearsalAccount) -> set[str]:
    return {
        message.id
        for message in account.gmail.stored.values()
        if message.made_by_rehearsal and not message.labels & {TRASH, DRAFT}
    }


def events_to_cancel(account: RehearsalAccount) -> set[str]:
    return {
        event_id
        for event_id, made in account.calendar.made_by_rehearsal.items()
        if made and account.calendar.status_of(event_id) != "cancelled"
    }


def untouched_messages(account: RehearsalAccount) -> dict[str, tuple[frozenset[str], str, bytes]]:
    return {
        message.id: (frozenset(message.labels), message.thread_id, message.rfc822)
        for message in account.gmail.stored.values()
        if not message.made_by_rehearsal
    }


def untouched_events(account: RehearsalAccount) -> dict[str, dict[str, Any]]:
    return {
        event_id: dict(item)
        for event_id, item in account.calendar.stored.items()
        if not account.calendar.made_by_rehearsal[event_id]
    }


def every_fixture_id(account: RehearsalAccount) -> set[str]:
    return set(account.gmail.stored) | set(account.calendar.stored)


# --- the mail ---


@dataclass(frozen=True)
class _Mail:
    id: str
    sender: str
    to: str
    subject: str
    labels: frozenset[str]
    thread: str
    minute: int
    made_by_rehearsal: bool
    headers: tuple[tuple[str, str], ...] = ()
    # The From domain Gmail's DMARC verdict names; None for the account's own sent mail.
    dmarc_domain: str | None = None
    dmarc: str = "pass"


@dataclass(frozen=True)
class _MailOptions:
    with_orphan_draft: bool
    evaluators_approval_thread: str


def _messages(options: _MailOptions) -> list[StoredMessage]:
    mails = [
        *_rehearsal_mail(),
        *_already_trashed_mail(),
        *_google_mail(),
        *_evaluators_mail(options.evaluators_approval_thread),
    ]
    if options.with_orphan_draft:
        mails.append(_orphan_draft())
    return [_stored(mail) for mail in mails] + [_lock_draft_message()]


def _rehearsal_mail() -> list[_Mail]:
    def ext(**fields: Any) -> _Mail:
        return _Mail(sender=S_EXT, to=ACCOUNT, dmarc_domain="protonmail.com", **fields)

    def internal(**fields: Any) -> _Mail:
        return _Mail(sender=S_INT, to=ACCOUNT, dmarc_domain="icloud.com", **fields)

    def sent(**fields: Any) -> _Mail:
        return _Mail(sender=ACCOUNT, **fields)

    budget = "Quick question about the Q4 budget"
    meeting = "Can we meet on Wednesday?"
    planning = "Planning call next week"
    return [
        internal(id="r-int", subject=budget, labels=_l(PROCESSED_INBOX), thread="t-shared",
                 minute=0, made_by_rehearsal=True),
        sent(id="r-ack", to=S_INT, subject=f"Re: {budget}", labels=_l({SENT, INBOX}),
             thread="t-shared", minute=2, made_by_rehearsal=True,
             headers=_aeca("acknowledgement", action_key("r-int", "reply"))),
        _Mail(id="r-ext-arch", sender="=?UTF-8?Q?Ionel_C=C3=AEmpan?= <Ionel.Cimpan@ProtonMail.com>",
              to=ACCOUNT, subject="Newsletter draft for review", labels=_l({PROCESSED_LABEL_ID}),
              thread="t-ext-arch", minute=4, made_by_rehearsal=True,
              dmarc_domain="protonmail.com"),
        ext(id="r-spam", subject="You have won a prize", labels=_l({SPAM}), thread="t-spam",
            minute=6, made_by_rehearsal=True),
        internal(id=R_MTG, subject=meeting, labels=_l(PROCESSED_INBOX), thread="t-mtg", minute=8,
                 made_by_rehearsal=True, headers=(("Message-ID", "<5E1F9A@icloud.com>"),)),
        sent(id="r-req", to=S_EXT, subject=f"[AECA] Approve: {meeting}", labels=_l({SENT}),
             thread="t-appr", minute=10, made_by_rehearsal=True,
             headers=_aeca("approval-request", approval_id(R_MTG, 0))),
        ext(id="r-approve", subject=f"Re: [AECA] Approve: {meeting}", labels=_l(PROCESSED_INBOX),
            thread="t-appr", minute=12, made_by_rehearsal=True),
        sent(id="r-conf", to=S_INT, subject=f"Re: {meeting}", labels=_l({SENT, INBOX}),
             thread="t-mtg", minute=14, made_by_rehearsal=True,
             headers=_aeca("confirmation", action_key(R_MTG, "confirm"))),
        ext(id="r-ext2", subject=planning, labels=_l(PROCESSED_INBOX), thread="t-ext2",
            minute=16, made_by_rehearsal=True),
        sent(id="r-req0", to=S_EXT, subject=f"[AECA] Approve: {planning}", labels=_l({SENT}),
             thread="t-req0", minute=18, made_by_rehearsal=True,
             headers=_aeca("approval-request", approval_id("r-ext2", 0))),
        sent(id="r-req1-self", to=ACCOUNT, subject=f"[AECA] Approve: {planning}",
             labels=_l({SENT}), thread="t-self", minute=20, made_by_rehearsal=True,
             headers=_aeca("approval-request", approval_id("r-ext2", 1))),
        sent(id="r-self-approve", to=ACCOUNT, subject=f"Re: [AECA] Approve: {planning}",
             labels=_l({SENT, INBOX}), thread="t-self", minute=22, made_by_rehearsal=True,
             headers=_replying_to("r-req1-self")),
        sent(id="r-alert", to=S_EXT, subject="[AECA] Alert: Google refused the token",
             labels=_l({SENT}), thread="t-alert", minute=24, made_by_rehearsal=True,
             headers=_aeca("alert", alert_id("credentials", date(2026, 11, 7)))),
        ext(id="r-q3", subject="Q3 report", labels=_l(PROCESSED_INBOX), thread="t-q3", minute=26,
            made_by_rehearsal=True),
        ext(id="r-transport", subject="[AECA test] transport check", labels=_l(PROCESSED_INBOX),
            thread="t-transport", minute=28, made_by_rehearsal=True),
        sent(id="r-req6", to=ACCOUNT, subject=f"[AECA] Approve: {planning}", labels=_l({SENT}),
             thread="t-req6", minute=30, made_by_rehearsal=True,
             headers=_aeca("approval-request", approval_id("r-ext2", 6))),
    ]  # fmt: skip


def _already_trashed_mail() -> list[_Mail]:
    return [
        _Mail(id="sp-1", sender=S_EXT, to=ACCOUNT, subject="[AECA spike] one",
              labels=_l({TRASH}), thread="t-sp1", minute=-60, made_by_rehearsal=True,
              dmarc_domain="protonmail.com"),
        _Mail(id="sp-2", sender=ACCOUNT, to=S_EXT, subject="[AECA spike] two",
              labels=_l({SENT, TRASH}), thread="t-sp2", minute=-58, made_by_rehearsal=True,
              headers=_aeca("acknowledgement", action_key("sp-1", "reply"))),
    ]  # fmt: skip


def _google_mail() -> list[_Mail]:
    def google(id: str, sender: str, labels: set[str]) -> _Mail:
        domain = sender.rpartition("@")[2].rstrip(">")
        return _Mail(id=id, sender=sender, to=ACCOUNT, subject=f"Google: {id}",
                     labels=_l(labels | {UPDATES}), thread=f"t-{id}", minute=-30,
                     made_by_rehearsal=False, dmarc_domain=domain)  # fmt: skip

    return [
        google("g-1", "Google <no-reply@accounts.google.com>", PROCESSED_INBOX),
        google("g-2", "no-reply@accounts.google.com", PROCESSED_INBOX),
        google("g-3", "community-noreply@google.com", PROCESSED_INBOX | {URGENT_LABEL_ID}),
        google("g-4", "Google Cloud <noreply@google.com>", PROCESSED_INBOX),
    ]


def _evaluators_mail(approval_thread: str) -> list[_Mail]:
    def grader(**fields: Any) -> _Mail:
        fields.setdefault("to", ACCOUNT)
        return _Mail(sender=GRADER, dmarc_domain="telekom.example", made_by_rehearsal=False,
                     **fields)  # fmt: skip

    def lookalike(id: str, address: str) -> _Mail:
        return _Mail(id=id, sender=address, to=ACCOUNT, subject=f"Hello from {id}",
                     labels=_l(PROCESSED_INBOX), thread=f"t-{id}", minute=70,
                     made_by_rehearsal=False, dmarc_domain=address.rpartition("@")[2])  # fmt: skip

    def own(**fields: Any) -> _Mail:
        fields.setdefault("to", ACCOUNT)
        return _Mail(sender=ACCOUNT, made_by_rehearsal=False, **fields)

    return [
        grader(id="ev-thread", subject="Quick question about the Q4 budget", labels=_l({INBOX}),
               thread="t-shared", minute=60, headers=(("Cc", S_EXT),)),
        own(id="ev-reply", to=GRADER, subject="Re: Quick question about the Q4 budget",
            labels=_l({SENT}), thread="t-shared", minute=62,
            headers=_aeca("acknowledgement", action_key("ev-thread", "reply"))),
        _Mail(id="ev-display", sender=f'"{S_EXT}" <{GRADER}>', to=ACCOUNT, subject="Who am I?",
              labels=_l(PROCESSED_INBOX), thread="t-ev-display", minute=64,
              made_by_rehearsal=False, dmarc_domain="telekom.example"),
        lookalike("ev-look-1", "xionel.cimpan@protonmail.com"),
        lookalike("ev-look-2", "ionel.cimpan@protonmail.com.evil.example"),
        lookalike("ev-look-3", "ionel.cimpan+aeca@protonmail.com"),
        lookalike("ev-look-4", "ionel.cimpan@proton.me"),
        lookalike("ev-look-5", "ionelcimpan@icloud.com"),
        _Mail(id="ev-multi", sender=f"{GRADER}, {S_EXT}", to=ACCOUNT, subject="Two senders",
              labels=_l(PROCESSED_INBOX), thread="t-ev-multi", minute=72,
              made_by_rehearsal=False, dmarc_domain="protonmail.com"),
        # The rehearsal address first, with Gmail's pass for its domain: only the single-sender
        # check keeps it out.
        _Mail(id="ev-multi-first", sender=f"{S_EXT}, {GRADER}", to=ACCOUNT,
              subject="Two senders, Ionel first", labels=_l(PROCESSED_INBOX),
              thread="t-ev-multi-first", minute=73, made_by_rehearsal=False,
              dmarc_domain="protonmail.com"),
        # The account's own mail with AECA's headers to a grader and Ionel together: R2 needs every
        # recipient to be a rehearsal sender.
        own(id="ev-mixed", to=GRADER, subject="Re: For both of you", labels=_l({SENT}),
            thread="t-ev-to-both", minute=75, headers=(("Cc", S_EXT),
            *_aeca("acknowledgement", action_key("ev-to-both", "reply")))),
        grader(id="ev-to-both", to=f"{ACCOUNT}, {S_EXT}", subject="For both of you",
               labels=_l(PROCESSED_INBOX), thread="t-ev-to-both", minute=74),
        grader(id="ev-replyto", subject="Reply elsewhere", labels=_l(PROCESSED_INBOX),
               thread="t-ev-replyto", minute=76, headers=(("Reply-To", S_EXT),)),
        grader(id="ev-forged", subject="[AECA] Approve: Can we meet on Wednesday?",
               labels=_l({INBOX}), thread="t-ev-forged", minute=78,
               headers=(*_aeca("approval-request", approval_id(R_MTG, 0)),
                        ("X-AECA-Test", "true"))),
        own(id="ev-self", subject=f"Lunch with {S_INT}?", labels=_l({SENT, INBOX}),
            thread="t-ev-self", minute=80),
        own(id="ev-req", subject="[AECA] Approve: Quick question about the Q4 budget",
            labels=_l({SENT}), thread=approval_thread, minute=82,
            headers=_aeca("approval-request", approval_id("ev-thread", 0))),
        own(id="ev-approve", subject="Re: [AECA] Approve: Quick question about the Q4 budget",
            labels=_l({SENT, INBOX}), thread=approval_thread, minute=84,
            headers=_replying_to("ev-req")),
        # A hand reply typed in a rehearsal approval thread, but answering the evaluators' email.
        own(id="ev-hand-reply", to=GRADER, subject="Re: Quick question about the Q4 budget",
            labels=_l({SENT}), thread="t-appr", minute=90, headers=_replying_to("ev-thread")),
        _Mail(id="ev-spoof", sender=S_EXT, to=ACCOUNT, subject="Spoofed from Proton",
              labels=_l({INBOX}), thread="t-ev-spoof", minute=86, made_by_rehearsal=False,
              dmarc_domain="protonmail.com", dmarc="fail"),
        _Mail(id="ev-spoof-domain", sender=S_INT, to=ACCOUNT, subject="Passed for another domain",
              labels=_l({INBOX}), thread="t-ev-spoof-domain", minute=88,
              made_by_rehearsal=False, dmarc_domain="evil.example"),
    ]  # fmt: skip


def _orphan_draft() -> _Mail:
    return _Mail(id="r-orphan-draft", sender=ACCOUNT, to=S_EXT, subject="[AECA] Approve: Q3 report",
                 labels=_l({DRAFT}), thread="t-orphan", minute=32, made_by_rehearsal=True,
                 headers=_aeca("approval-request", approval_id("r-q3", 0)))  # fmt: skip


def _lock_draft_message() -> StoredMessage:
    """The lock draft as messages.list shows it: a DRAFT with no recipient."""
    mime = EmailMessage()
    mime["Subject"] = LOCK_SUBJECT
    mime["X-AECA-Kind"] = "lock"
    mime.set_content(f"copy_id: {OTHER_COPY}\nheartbeat: {NOW.isoformat()}\n")
    return StoredMessage(
        id="d-lock",
        thread_id="t-lock",
        labels={DRAFT},
        rfc822=mime.as_bytes(),
        internal_date=NOW - timedelta(days=1),
        made_by_rehearsal=False,
    )


def _stored(mail: _Mail) -> StoredMessage:
    when = datetime(2026, 11, 7, 9, 0, tzinfo=UTC) + timedelta(minutes=mail.minute)
    raw = make_raw(
        sender=mail.sender,
        to=mail.to,
        subject=mail.subject,
        body=BODY_PREFIX + mail.id,
        headers={"Message-ID": _rfc822_id(mail.id), **dict(mail.headers)},
        authentication_results=_verdict(mail),
        label_ids=mail.labels,
        message_id=mail.id,
        thread_id=mail.thread,
        internal_date=when,
    )
    return StoredMessage(
        id=mail.id,
        thread_id=mail.thread,
        labels=set(mail.labels),
        rfc822=raw.rfc822,
        internal_date=when,
        made_by_rehearsal=mail.made_by_rehearsal,
    )


def _verdict(mail: _Mail) -> list[str]:
    if mail.dmarc_domain is None:
        return []
    domain = mail.dmarc_domain
    return [
        f"mx.google.com; dkim=pass header.i=@{domain}; spf=pass smtp.mailfrom={domain}; "
        f"dmarc={mail.dmarc} (p=QUARANTINE dis=NONE) header.from={domain}"
    ]


def _rfc822_id(message_id: str) -> str:
    return f"<{message_id}@fixture.test>"


def _replying_to(message_id: str) -> tuple[tuple[str, str], ...]:
    return (("In-Reply-To", _rfc822_id(message_id)), ("References", _rfc822_id(message_id)))


def _aeca(kind: str, aeca_id: str) -> tuple[tuple[str, str], ...]:
    return (("X-AECA-Kind", kind), ("X-AECA-Id", aeca_id))


def _l(labels: set[str]) -> frozenset[str]:
    return frozenset(labels)


def _iso(moment: datetime | None) -> str:
    return "" if moment is None else moment.isoformat()


# --- the calendar ---


def _fill_calendar(calendar: FakeCalendarService) -> None:
    seeder = GoogleSeedCalendar(calendar, ACCOUNT)
    seed_calendar(seeder, SeedWeek(week_start=SEEDED_WEEK, zone=BUCHAREST))
    _seed_one_event_of_the_second_week(calendar)
    for event_id_ in list(calendar.stored):
        calendar.made_by_rehearsal[event_id_] = True
    for body, made_by_rehearsal in _other_events():
        calendar.add(body, made_by_rehearsal=made_by_rehearsal)


def _seed_one_event_of_the_second_week(calendar: FakeCalendarService) -> None:
    week = FakeCalendarService(journal=WriteJournal())
    seed_calendar(GoogleSeedCalendar(week, ACCOUNT), SeedWeek(SECOND_SEEDED_WEEK, BUCHAREST))
    first_id = next(iter(week.stored))
    calendar.stored[first_id] = week.stored[first_id]


def _other_events() -> list[tuple[dict[str, Any], bool]]:
    lower_q3_attempts = [
        (_timed(event_id("r-q3", attempt), "Q3 review", "2026-11-12T11:00:00+02:00", "cancelled"),
         True)
        for attempt in range(MAX_EVENT_ATTEMPTS - 1)
    ]  # fmt: skip
    return [
        (_timed(E_MTG, "Meeting: Can we meet on Wednesday?", "2026-11-11T10:00:00+02:00"), True),
        (_timed(EVENT_EXT2_ATTEMPT_0, "Planning call", "2026-11-19T10:00:00+02:00", "cancelled"),
         True),
        (_timed(E_EXT2_1, "Planning call", "2026-11-20T10:00:00+02:00"), True),
        *lower_q3_attempts,
        (_timed(E_MAX, "Q3 review", "2026-11-12T11:00:00+02:00"), True),
        ({**_timed(SPIKE_EVENT, "[AECA test] spike", "2026-11-05T09:00:00+02:00", "cancelled"),
          "extendedProperties": {"private": {"aeca_test": "true"}}}, True),
        (_timed(EV_EVENT, "Meeting: evaluators", "2026-11-11T15:00:00+02:00"), False),
        ({**_timed(EV_INVITE, "[AECA test] Sync with grader", "2026-11-12T16:00:00+02:00"),
          "organizer": {"email": GRADER}}, False),
        ({**_timed(MANUAL_EVENT, "Dentist", "2026-11-13T08:00:00+02:00"),
          "description": "approval a-0123456789ab"}, False),
        (_timed(LOOKALIKE_EVENT, "Something else", "2026-11-13T12:00:00+02:00"), False),
    ]  # fmt: skip


def _timed(event_id_: str, summary: str, start: str, status: str = "confirmed") -> dict[str, Any]:
    starts = datetime.fromisoformat(start)
    return {
        "id": event_id_,
        "summary": summary,
        "status": status,
        "start": {"dateTime": starts.isoformat()},
        "end": {"dateTime": (starts + timedelta(hours=1)).isoformat()},
    }
