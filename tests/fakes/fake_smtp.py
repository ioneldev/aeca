"""An SMTP server that keeps what it was sent, so a test never opens a connection."""

from email.message import EmailMessage


class FakeSmtpConnection:
    def __init__(self, failure: Exception | None = None) -> None:
        self.logins: list[tuple[str, str]] = []
        self.sent: list[EmailMessage] = []
        self.closed = False
        self._failure = failure

    def login(self, user: str, password: str) -> None:
        if self._failure is not None:
            raise self._failure
        self.logins.append((user, password))

    def send_message(self, message: EmailMessage) -> None:
        self.sent.append(message)

    def quit(self) -> None:
        self.closed = True


class FakeSmtp:
    """Hands out one connection per connect(), like smtplib would."""

    def __init__(self, failure: Exception | None = None) -> None:
        self.connections: list[FakeSmtpConnection] = []
        self._failure = failure

    def connect(self) -> FakeSmtpConnection:
        connection = FakeSmtpConnection(self._failure)
        self.connections.append(connection)
        return connection

    @property
    def sent(self) -> list[EmailMessage]:
        return [message for connection in self.connections for message in connection.sent]
