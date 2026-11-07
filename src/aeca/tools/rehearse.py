"""Sends the deliverable corpus emails as real mail from the two rehearsal accounts."""

import smtplib
import ssl
import time
from collections.abc import Callable
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Protocol

from aeca.domain.errors import AecaError
from aeca.settings import MailAccount, RehearsalAccounts
from aeca.tools.corpus import CorpusEmail, to_mime

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 465
SMTP_TIMEOUT_SECONDS = 30
SECONDS_BETWEEN_EMAILS = 2


class SmtpConnection(Protocol):
    def login(self, user: str, password: str) -> object: ...

    def send_message(self, message: EmailMessage) -> object: ...

    def quit(self) -> object: ...


class RehearsalSendError(AecaError):
    """Gmail refused a rehearsal email. Those before it were already delivered."""


def connect_to_gmail() -> SmtpConnection:
    return smtplib.SMTP_SSL(
        SMTP_HOST, SMTP_PORT, context=ssl.create_default_context(), timeout=SMTP_TIMEOUT_SECONDS
    )


@dataclass(frozen=True)
class SmtpSender:
    accounts: RehearsalAccounts
    recipient: str
    connect: Callable[[], SmtpConnection] = connect_to_gmail

    def send(self, email: CorpusEmail, *, parent: CorpusEmail | None) -> None:
        account = self._account_for(email)
        message = to_mime(email, account.address, recipient=self.recipient, parent=parent)
        try:
            self._deliver(message, account)
        except (smtplib.SMTPException, OSError) as error:
            raise _refused(email, account, error) from error

    def _account_for(self, email: CorpusEmail) -> MailAccount:
        return self.accounts.internal if email.sender_role == "internal" else self.accounts.external

    def _deliver(self, message: EmailMessage, account: MailAccount) -> None:
        connection = self.connect()
        try:
            connection.login(account.address, account.app_password.reveal())
            connection.send_message(message)
        finally:
            connection.quit()


def rehearse(
    corpus: tuple[CorpusEmail, ...],
    sender: SmtpSender,
    *,
    sleep: Callable[[float], None] = time.sleep,
) -> list[str]:
    """Ids of the emails sent, in corpus order. Gmail would filter the undeliverable ones, so
    sending them would only test Gmail."""
    by_id = {email.id: email for email in corpus}
    sent: list[str] = []
    for email in (email for email in corpus if email.deliverable):
        if sent:
            sleep(SECONDS_BETWEEN_EMAILS)
        sender.send(email, parent=by_id.get(email.follows) if email.follows else None)
        sent.append(email.id)
    return sent


def _refused(email: CorpusEmail, account: MailAccount, error: Exception) -> RehearsalSendError:
    return RehearsalSendError(
        f"Gmail refused {email.id!r} from {account.address}: {type(error).__name__}.",
        next_action=(
            "Check that the account has 2-Step Verification and that its app password in .env is "
            "current. Emails before this one were already delivered."
        ),
    )
