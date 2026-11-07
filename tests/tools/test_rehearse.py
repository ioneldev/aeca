from smtplib import SMTPAuthenticationError

import pytest
from fakes.fake_smtp import FakeSmtp
from support.corpus import make_corpus_email

from aeca.domain.errors import AecaError
from aeca.domain.secret import SecretStr
from aeca.settings import MailAccount, RehearsalAccounts
from aeca.tools.rehearse import SmtpSender, rehearse

ACCOUNTS = RehearsalAccounts(
    internal=MailAccount("insider@gmail.test", SecretStr("internal-app-password-1")),
    external=MailAccount("outsider@gmail.test", SecretStr("external-app-password-2")),
)
RECIPIENT = "aeca@gmail.test"


def sender_over(smtp: FakeSmtp) -> SmtpSender:
    return SmtpSender(ACCOUNTS, recipient=RECIPIENT, connect=smtp.connect)


def run(corpus, smtp: FakeSmtp | None = None):  # type: ignore[no-untyped-def]
    smtp = smtp or FakeSmtp()
    sleeps: list[float] = []
    sent = rehearse(corpus, sender_over(smtp), sleep=sleeps.append)
    return sent, smtp, sleeps


def test_rehearsal_mail_carries_the_test_header_and_the_role_sender() -> None:
    corpus = (
        make_corpus_email(id="boss", sender_role="internal"),
        make_corpus_email(id="stranger", sender_role="external"),
    )

    _, smtp, _ = run(corpus)

    boss, stranger = smtp.sent
    assert boss["X-AECA-Test"] == stranger["X-AECA-Test"] == "rehearsal"
    assert "insider@gmail.test" in boss["From"] and "outsider@gmail.test" in stranger["From"]
    assert boss["To"] == stranger["To"] == RECIPIENT


def test_each_role_logs_in_with_its_own_app_password() -> None:
    corpus = (
        make_corpus_email(id="boss", sender_role="internal"),
        make_corpus_email(id="stranger", sender_role="external"),
    )

    _, smtp, _ = run(corpus)

    logins = [login for connection in smtp.connections for login in connection.logins]
    assert logins == [
        ("insider@gmail.test", "internal-app-password-1"),
        ("outsider@gmail.test", "external-app-password-2"),
    ]


def test_bulk_and_automated_roles_send_from_the_external_account() -> None:
    corpus = (
        make_corpus_email(id="news", sender_role="bulk"),
        make_corpus_email(id="auto", sender_role="automated"),
    )

    _, smtp, _ = run(corpus)

    assert all("outsider@gmail.test" in message["From"] for message in smtp.sent)


def test_undeliverable_emails_are_never_sent() -> None:
    corpus = (
        make_corpus_email(id="ok"),
        make_corpus_email(id="phish", deliverable=False),
        make_corpus_email(id="forged", sender_role="internal", deliverable=False),
    )

    sent, smtp, _ = run(corpus)

    assert sent == ["ok"]
    assert [m["Message-ID"] for m in smtp.sent] == ["<corpus-ok@aeca.test>"]


def test_emails_go_out_in_corpus_order_two_seconds_apart() -> None:
    corpus = tuple(make_corpus_email(id=name) for name in ("a", "b", "c"))

    sent, _, sleeps = run(corpus)

    assert sent == ["a", "b", "c"]
    assert sleeps == [2, 2]


def test_a_follow_up_is_sent_as_a_reply_in_the_thread() -> None:
    corpus = (
        make_corpus_email(id="first", subject="Price list"),
        make_corpus_email(id="second", subject="More", follows="first"),
    )

    _, smtp, _ = run(corpus)

    reply = smtp.sent[1]
    assert reply["Subject"] == "Re: Price list"
    assert reply["In-Reply-To"] == "<corpus-first@aeca.test>"


def test_every_connection_is_closed() -> None:
    _, smtp, _ = run((make_corpus_email(id="a"), make_corpus_email(id="b")))

    assert all(connection.closed for connection in smtp.connections)


def test_a_refused_login_stops_the_run_and_never_shows_the_password() -> None:
    smtp = FakeSmtp(
        failure=SMTPAuthenticationError(535, b"5.7.8 Username and Password not accepted")
    )

    with pytest.raises(AecaError) as raised:
        run((make_corpus_email(id="a", sender_role="internal"),), smtp)

    assert "internal-app-password-1" not in str(raised.value)
    assert "insider@gmail.test" in str(raised.value) and raised.value.next_action
    assert all(connection.closed for connection in smtp.connections)


def test_each_id_is_reported_as_it_is_sent() -> None:
    corpus = (make_corpus_email(id="a"), make_corpus_email(id="b"))
    reported: list[str] = []

    rehearse(corpus, sender_over(FakeSmtp()), sleep=lambda _: None, on_sent=reported.append)

    assert reported == ["a", "b"]


def test_a_failure_names_the_emails_already_delivered() -> None:
    smtp = FakeSmtp(fail_on_connection=2)
    corpus = tuple(make_corpus_email(id=name) for name in ("a", "b", "c"))

    with pytest.raises(AecaError) as raised:
        rehearse(corpus, sender_over(smtp), sleep=lambda _: None)

    message = str(raised.value)
    assert "'b'" in message and "Delivered before it: a" in message
    assert raised.value.next_action
