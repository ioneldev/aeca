import json
from pathlib import Path

import pytest
from fakes.fake_script_runner import FakeScriptRunner
from support.corpus import make_corpus_email

from aeca.domain.errors import AecaError, ConfigError
from aeca.settings import RehearsalSenders
from aeca.tools.corpus import load_corpus
from aeca.tools.rehearse import (
    MailAppSender,
    OsascriptRunner,
    RehearsalSendError,
    ScriptFailed,
    plan_rehearsal,
    rehearse,
)

SENDERS = RehearsalSenders(internal="insider@mail.test", external="outsider@mail.test")
RECIPIENT = "aeca@gmail.test"
REAL_CORPUS = Path(__file__).resolve().parents[1] / "corpus" / "emails.json"
SENDER, TO, SUBJECT, BODY, FOLLOWED = range(5)


def sender_over(runner: FakeScriptRunner) -> MailAppSender:
    return MailAppSender(SENDERS, recipient=RECIPIENT, runner=runner)


def run(corpus, runner: FakeScriptRunner | None = None):  # type: ignore[no-untyped-def]
    runner = runner or FakeScriptRunner()
    sleeps: list[float] = []
    sent = rehearse(corpus, sender_over(runner), sleep=sleeps.append)
    return sent, runner, sleeps


# --- which emails go -------------------------------------------------------------------------


def test_the_real_corpus_sends_27_emails_and_leaves_8_for_eval_only() -> None:
    plan = plan_rehearsal(load_corpus(REAL_CORPUS))

    assert len(plan.to_send) == 27
    assert len(plan.eval_only_ids) == 8
    assert "sales-mail-merge-headers" in plan.eval_only_ids
    assert "marketing-newsletter-html-only" in plan.eval_only_ids
    assert "phishing-content-only" in plan.eval_only_ids


def test_only_deliverable_plain_emails_without_custom_headers_are_eligible() -> None:
    corpus = (
        make_corpus_email(id="plain"),
        make_corpus_email(id="html", html=True),
        make_corpus_email(id="headed", headers={"List-Unsubscribe": "<mailto:x@y.test>"}),
        make_corpus_email(id="phish", deliverable=False),
    )

    plan = plan_rehearsal(corpus)

    assert [email.id for email in plan.to_send] == ["plain"]
    assert plan.eval_only_ids == ("html", "headed", "phish")


def test_undeliverable_and_html_emails_are_never_sent() -> None:
    corpus = (
        make_corpus_email(id="ok"),
        make_corpus_email(id="html", html=True),
        make_corpus_email(id="forged", sender_role="internal", deliverable=False),
    )

    sent, runner, _ = run(corpus)

    assert sent == ["ok"]
    assert len(runner.runs) == 1


# --- what is sent ----------------------------------------------------------------------------


def test_each_role_sends_from_its_own_address_to_the_gmail_account() -> None:
    corpus = (
        make_corpus_email(id="boss", sender_role="internal"),
        make_corpus_email(id="stranger", sender_role="external"),
        make_corpus_email(id="news", sender_role="bulk"),
        make_corpus_email(id="auto", sender_role="automated"),
    )

    _, runner, _ = run(corpus)

    senders = [r.arguments[SENDER] for r in runner.runs]
    assert senders == ["insider@mail.test"] + ["outsider@mail.test"] * 3
    assert {r.arguments[TO] for r in runner.runs} == {RECIPIENT}


def test_the_script_sets_sender_subject_content_and_sends_invisibly() -> None:
    _, runner, _ = run((make_corpus_email(id="a", subject="Price list", body="Hello\nthere"),))

    recorded = runner.runs[0]
    assert recorded.arguments[SUBJECT] == "Price list"
    assert recorded.arguments[BODY] == "Hello\nthere"
    for fragment in (
        "make new outgoing message",
        "visible:false",
        "make new to recipient",
        "send ",
    ):
        assert fragment in recorded.script


def test_values_travel_as_arguments_so_a_hostile_subject_cannot_break_out_of_the_script() -> None:
    hostile = 'Quote " and slash \\ then " & (do shell script "id") & "'
    email = make_corpus_email(id="a", subject=hostile, body=hostile)

    _, runner, _ = run((email,))

    recorded = runner.runs[0]
    assert hostile not in recorded.script
    assert recorded.arguments[SUBJECT] == recorded.arguments[BODY] == hostile
    assert "do shell script" not in recorded.script


def test_the_script_is_identical_for_every_new_message() -> None:
    _, runner, _ = run((make_corpus_email(id="a", subject="One"), make_corpus_email(id="b")))

    assert runner.runs[0].script == runner.runs[1].script


def test_an_email_without_attachments_passes_only_the_five_fixed_arguments() -> None:
    _, runner, _ = run((make_corpus_email(id="a"),))

    assert len(runner.runs[0].arguments) == 5


def test_attachments_are_placeholder_files_of_the_stated_type_that_are_cleaned_up_after() -> None:
    email = make_corpus_email(
        id="inv", attachments=[{"name": "INV-1.pdf", "mime_type": "application/pdf"}]
    )

    _, runner, _ = run((email,))

    recorded = runner.runs[0]
    (path,) = (Path(argument) for argument in recorded.arguments[5:])
    assert path.name == "INV-1.pdf"
    assert recorded.attachment_contents["INV-1.pdf"].startswith(b"%PDF-")
    assert "make new attachment" in recorded.script
    assert "at after the last paragraph of content" in recorded.script
    assert not path.exists() and not path.parent.exists()


def test_attachments_are_cleaned_up_when_the_send_fails() -> None:
    email = make_corpus_email(
        id="inv", attachments=[{"name": "INV-1.pdf", "mime_type": "application/pdf"}]
    )
    runner = FakeScriptRunner(fail_on_run=1)

    with pytest.raises(RehearsalSendError):
        run((email,), runner)

    assert not Path(runner.runs[0].arguments[5]).parent.exists()


def test_an_attachment_name_cannot_escape_the_temporary_folder() -> None:
    email = make_corpus_email(
        id="inv", attachments=[{"name": "../../evil.txt", "mime_type": "text/plain"}]
    )

    _, runner, _ = run((email,))

    assert set(runner.runs[0].attachment_contents) == {"evil.txt"}


# --- replies ---------------------------------------------------------------------------------


def follow_up_corpus():  # type: ignore[no-untyped-def]
    return (
        make_corpus_email(id="first", subject="Price list"),
        make_corpus_email(id="second", subject="More", body="Following up", follows="first"),
    )


def test_a_follow_up_looks_in_the_senders_sent_mailbox_and_replies_to_the_earlier_message() -> None:
    _, runner, _ = run(follow_up_corpus())

    reply = runner.runs[1]
    assert reply.arguments[FOLLOWED] == "Price list"
    assert reply.arguments[BODY] == "Following up"
    for fragment in ("sent mailbox", "whose subject is", "reply ", "set content of"):
        assert fragment in reply.script


def test_the_sending_account_is_found_with_a_loop_not_a_whose_filter() -> None:
    _, runner, _ = run(follow_up_corpus())

    for run_of_script in runner.runs:
        assert "repeat with mailAccount in every account" in run_of_script.script
        assert "(email addresses of mailAccount) contains senderAddress" in run_of_script.script
        assert "accounts whose" not in run_of_script.script
        assert "AECA_NO_MAIL_ACCOUNT" in run_of_script.script


def test_a_follow_up_falls_back_to_a_new_re_message_when_the_earlier_one_is_not_in_sent() -> None:
    _, runner, _ = run(follow_up_corpus())

    reply = runner.runs[1]
    assert reply.arguments[SUBJECT] == "Re: Price list"
    assert "make new outgoing message" in reply.script
    assert reply.script.index("reply ") < reply.script.index("make new outgoing message")


def test_the_reply_is_still_addressed_only_to_the_gmail_account() -> None:
    _, runner, _ = run(follow_up_corpus())

    reply = runner.runs[1]
    assert reply.arguments[TO] == RECIPIENT
    assert "delete every to recipient" in reply.script


def test_a_first_email_does_not_look_for_an_earlier_message() -> None:
    _, runner, _ = run(follow_up_corpus())

    assert "sent mailbox" not in runner.runs[0].script
    assert runner.runs[0].arguments[FOLLOWED] == ""


def test_every_script_stops_when_mail_has_no_account_for_the_sender() -> None:
    _, runner, _ = run(follow_up_corpus())

    assert all("email addresses" in r.script and "error " in r.script for r in runner.runs)


# --- pacing and reporting --------------------------------------------------------------------


def test_emails_go_out_in_corpus_order_two_seconds_apart() -> None:
    corpus = tuple(make_corpus_email(id=name, subject=name) for name in ("a", "b", "c"))

    sent, runner, sleeps = run(corpus)

    assert sent == ["a", "b", "c"]
    assert [r.arguments[SUBJECT] for r in runner.runs] == ["a", "b", "c"]
    assert sleeps == [2, 2]


def test_each_id_is_reported_as_it_is_sent() -> None:
    corpus = (make_corpus_email(id="a"), make_corpus_email(id="b"))
    reported: list[str] = []

    rehearse(corpus, sender_over(FakeScriptRunner()), sleep=lambda _: None, on_sent=reported.append)

    assert reported == ["a", "b"]


# --- failures --------------------------------------------------------------------------------


def test_a_failure_names_the_email_the_stderr_and_the_emails_already_delivered() -> None:
    runner = FakeScriptRunner(fail_on_run=2, stderr="Mail got an error: not allowed")
    corpus = tuple(make_corpus_email(id=name) for name in ("a", "b", "c"))

    with pytest.raises(AecaError) as raised:
        rehearse(corpus, sender_over(runner), sleep=lambda _: None)

    message = str(raised.value)
    assert "'b'" in message and "not allowed" in message
    assert "Delivered before it: a" in message
    assert raised.value.next_action
    assert len(runner.runs) == 2


def test_a_missing_mail_account_says_how_to_fix_it() -> None:
    runner = FakeScriptRunner(fail_on_run=1, stderr="execution error: AECA_NO_MAIL_ACCOUNT (1)")

    with pytest.raises(RehearsalSendError) as raised:
        run((make_corpus_email(id="a", sender_role="internal"),), runner)

    assert "insider@mail.test" in str(raised.value)
    assert "Add the address to Mail.app" in raised.value.next_action
    assert "REHEARSAL_*_SENDER" in raised.value.next_action


# --- the osascript boundary ------------------------------------------------------------------


class FinishedProcess:
    def __init__(self, returncode: int, stdout: str = "", stderr: str = "") -> None:
        self.returncode, self.stdout, self.stderr = returncode, stdout, stderr


def test_osascript_gets_the_script_then_the_arguments() -> None:
    calls: list[list[str]] = []

    def execute(command, **_):  # type: ignore[no-untyped-def]
        calls.append(command)
        return FinishedProcess(0, "ok\n")

    output = OsascriptRunner(execute=execute).run("SCRIPT", ["a@b.test", "-dash"])

    assert calls == [["osascript", "-e", "SCRIPT", "a@b.test", "-dash"]]
    assert output == "ok\n"


def test_a_non_zero_exit_raises_with_the_stderr_text() -> None:
    def execute(command, **_):  # type: ignore[no-untyped-def]
        return FinishedProcess(1, stderr="  Mail got an error: no account\n")

    with pytest.raises(ScriptFailed, match="Mail got an error: no account"):
        OsascriptRunner(execute=execute).run("SCRIPT")


def test_a_missing_osascript_raises_script_failed() -> None:
    def execute(command, **_):  # type: ignore[no-untyped-def]
        raise FileNotFoundError("osascript")

    with pytest.raises(ScriptFailed, match="osascript"):
        OsascriptRunner(execute=execute).run("SCRIPT")


# --- the SMTP route is gone ------------------------------------------------------------------


def test_no_app_password_setting_or_smtp_code_remains() -> None:
    root = Path(__file__).resolve().parents[2]
    shipped = [
        root / ".env.example",
        root / "src/aeca/settings.py",
        root / "src/aeca/tools/rehearse.py",
    ]

    for path in shipped:
        text = path.read_text(encoding="utf-8")
        assert "APP_PASSWORD" not in text and "smtplib" not in text, path
    assert not (root / "tests/fakes/fake_smtp.py").exists()
    assert json.loads(REAL_CORPUS.read_text(encoding="utf-8"))


def test_a_reply_gets_the_same_subject_as_the_fallback_message() -> None:
    _, runner, _ = run(follow_up_corpus())

    assert "set subject of outgoing to messageSubject" in runner.runs[1].script


def test_attachments_survive_until_the_whole_run_has_finished() -> None:
    corpus = (
        make_corpus_email(
            id="inv", attachments=[{"name": "INV-1.pdf", "mime_type": "application/pdf"}]
        ),
        make_corpus_email(id="after"),
    )
    runner = FakeScriptRunner()
    still_there: list[bool] = []

    rehearse(
        corpus,
        sender_over(runner),
        sleep=lambda _: None,
        on_sent=lambda _id: still_there.append(Path(runner.runs[0].arguments[5]).exists()),
    )

    assert still_there == [True, True]
    assert not Path(runner.runs[0].arguments[5]).parent.exists()


def test_two_emails_with_the_same_attachment_name_do_not_overwrite_each_other() -> None:
    attachment = [{"name": "same.pdf", "mime_type": "application/pdf"}]
    corpus = (
        make_corpus_email(id="a", attachments=attachment),
        make_corpus_email(id="b", attachments=attachment),
    )

    _, runner, _ = run(corpus)

    assert runner.runs[0].arguments[5] != runner.runs[1].arguments[5]


def test_the_run_refuses_when_a_sender_is_the_gmail_account() -> None:
    sender = MailAppSender(
        RehearsalSenders(internal="in@mail.test", external="Aeca.Gmail@gmail.com"),
        recipient="aecagmail@gmail.com",
        runner=FakeScriptRunner(),
    )

    with pytest.raises(ConfigError) as raised:
        rehearse((make_corpus_email(id="a"),), sender, sleep=lambda _: None)

    assert "GMAIL_ADDRESS" in str(raised.value) and raised.value.next_action
    assert sender.runner.runs == []  # type: ignore[attr-defined]


def test_the_run_refuses_when_both_senders_are_the_same_address() -> None:
    sender = MailAppSender(
        RehearsalSenders(internal="same@mail.test", external="SAME@mail.test"),
        recipient=RECIPIENT,
        runner=FakeScriptRunner(),
    )

    with pytest.raises(ConfigError, match="same address"):
        rehearse((make_corpus_email(id="a"),), sender, sleep=lambda _: None)
