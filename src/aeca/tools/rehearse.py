"""Sends the plain deliverable corpus emails as real mail, through Mail.app, from the two rehearsal
addresses. Mail.app can set neither custom headers nor HTML, so the emails that need them are left
to the eval, and the rehearsal mail is told apart by its sender address.
"""

import subprocess
import tempfile
import time
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from aeca.domain.errors import AecaError
from aeca.settings import RehearsalSenders
from aeca.tools.corpus import Attachment, CorpusEmail

SECONDS_BETWEEN_EMAILS = 2
OSASCRIPT_TIMEOUT_SECONDS = 120
NO_MAIL_ACCOUNT_MARKER = "AECA_NO_MAIL_ACCOUNT"
REPLY_PREFIX = "Re: "
FIXED_ARGUMENT_COUNT = 5

# Values reach the script as `on run argv` items and never inside a string literal, so no subject
# or body can break out of it. Items 1-5: sender, recipient, subject, body, followed subject ("" for
# a first email). Items 6 onwards: attachment paths.
_SCRIPT_TEMPLATE = f"""on run argv
    set senderAddress to item 1 of argv
    set recipientAddress to item 2 of argv
    set messageSubject to item 3 of argv
    set messageBody to item 4 of argv
    set followedSubject to item 5 of argv
    tell application "Mail"
        if (count of (accounts whose email addresses contains senderAddress)) is 0 then
            error "{NO_MAIL_ACCOUNT_MARKER}"
        end if
@COMPOSE@
        tell outgoing
            make new to recipient at end of to recipients with properties {{address:recipientAddress}}
            repeat with argumentIndex from {FIXED_ARGUMENT_COUNT + 1} to (count of argv)
                make new attachment with properties {{file name:(POSIX file (item argumentIndex of argv))}} at after the last paragraph
            end repeat
        end tell
        send outgoing
    end tell
end run
"""  # noqa: E501 (AppleScript lines are not wrapped)

_NEW_MESSAGE = """        set outgoing to make new outgoing message with properties {subject:messageSubject, content:messageBody, sender:senderAddress, visible:false}"""  # noqa: E501

# Mail's `reply` sets In-Reply-To, which is what threads the follow-up on the Gmail side.
_REPLY_OR_NEW_MESSAGE = f"""        set earlierMessages to (messages of sent mailbox whose subject is followedSubject and sender contains senderAddress)
        if (count of earlierMessages) > 0 then
            set outgoing to reply (item 1 of earlierMessages) opening window false
            set content of outgoing to messageBody
            set sender of outgoing to senderAddress
            delete every to recipient of outgoing
        else
{_NEW_MESSAGE.replace("        set", "            set", 1)}
        end if"""  # noqa: E501

NEW_MESSAGE_SCRIPT = _SCRIPT_TEMPLATE.replace("@COMPOSE@", _NEW_MESSAGE)
REPLY_SCRIPT = _SCRIPT_TEMPLATE.replace("@COMPOSE@", _REPLY_OR_NEW_MESSAGE)

_PLACEHOLDER_BY_MIME_TYPE = {"application/pdf": b"%PDF-1.4\n% placeholder attachment\n%%EOF\n"}


class RehearsalSendError(AecaError):
    """Mail.app could not send a rehearsal email. Those before it were already delivered."""


class ScriptFailed(Exception):
    """The script runner failed; the text says why, as osascript printed it."""


class ScriptRunner(Protocol):
    def run(self, script: str, arguments: Sequence[str] = ()) -> str: ...


@dataclass(frozen=True)
class OsascriptRunner:
    """The one place that starts osascript."""

    execute: Callable[..., Any] = subprocess.run

    def run(self, script: str, arguments: Sequence[str] = ()) -> str:
        command = ["osascript", "-e", script, *arguments]
        try:
            finished = self.execute(
                command,
                capture_output=True,
                text=True,
                check=False,
                timeout=OSASCRIPT_TIMEOUT_SECONDS,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise ScriptFailed(f"osascript did not run: {error}") from error
        if finished.returncode != 0:
            raise ScriptFailed(finished.stderr.strip() or "osascript failed without a message")
        return str(finished.stdout)


@dataclass(frozen=True)
class RehearsalPlan:
    to_send: tuple[CorpusEmail, ...]
    eval_only_ids: tuple[str, ...]


def plan_rehearsal(corpus: tuple[CorpusEmail, ...]) -> RehearsalPlan:
    to_send = tuple(email for email in corpus if _can_be_sent_by_mail_app(email))
    sent_ids = {email.id for email in to_send}
    return RehearsalPlan(
        to_send=to_send,
        eval_only_ids=tuple(email.id for email in corpus if email.id not in sent_ids),
    )


def _can_be_sent_by_mail_app(email: CorpusEmail) -> bool:
    """Gmail would filter an undeliverable email, so sending it would only test Gmail."""
    return email.deliverable and not email.headers and not email.html


@dataclass(frozen=True)
class MailAppSender:
    senders: RehearsalSenders
    recipient: str
    runner: ScriptRunner

    def send(self, email: CorpusEmail, *, parent: CorpusEmail | None) -> None:
        sender = self._sender_address_for(email)
        with _placeholder_files(email.attachments) as attachment_paths:
            script, arguments = self._script_for(email, sender, parent, attachment_paths)
            try:
                self.runner.run(script, arguments)
            except ScriptFailed as failed:
                raise _refused(email, sender, str(failed)) from failed

    def _sender_address_for(self, email: CorpusEmail) -> str:
        return self.senders.internal if email.sender_role == "internal" else self.senders.external

    def _script_for(
        self,
        email: CorpusEmail,
        sender: str,
        parent: CorpusEmail | None,
        attachment_paths: list[Path],
    ) -> tuple[str, list[str]]:
        subject = email.subject if parent is None else f"{REPLY_PREFIX}{parent.subject}"
        followed_subject = "" if parent is None else parent.subject
        script = NEW_MESSAGE_SCRIPT if parent is None else REPLY_SCRIPT
        arguments = [sender, self.recipient, subject, email.body, followed_subject]
        return script, [*arguments, *map(str, attachment_paths)]


def rehearse(
    corpus: tuple[CorpusEmail, ...],
    sender: MailAppSender,
    *,
    sleep: Callable[[float], None] = time.sleep,
    on_sent: Callable[[str], None] = lambda _email_id: None,
) -> list[str]:
    """Ids of the emails sent, in corpus order."""
    by_id = {email.id: email for email in corpus}
    sent: list[str] = []
    for email in plan_rehearsal(corpus).to_send:
        if sent:
            sleep(SECONDS_BETWEEN_EMAILS)
        try:
            sender.send(email, parent=by_id.get(email.follows) if email.follows else None)
        except RehearsalSendError as refused:
            raise _with_delivered(refused, sent) from refused
        sent.append(email.id)
        on_sent(email.id)
    return sent


@contextmanager
def _placeholder_files(attachments: list[Attachment]) -> Iterator[list[Path]]:
    with tempfile.TemporaryDirectory(prefix="aeca-rehearsal-") as folder:
        paths = [_write_placeholder(Path(folder), attachment) for attachment in attachments]
        yield paths


def _write_placeholder(folder: Path, attachment: Attachment) -> Path:
    path = folder / Path(attachment.name).name
    default = f"placeholder for {attachment.name}".encode()
    path.write_bytes(_PLACEHOLDER_BY_MIME_TYPE.get(attachment.mime_type, default))
    return path


def _with_delivered(refused: RehearsalSendError, sent: list[str]) -> RehearsalSendError:
    delivered = ", ".join(sent) or "none"
    return RehearsalSendError(
        f"{refused} Delivered before it: {delivered}.", next_action=refused.next_action
    )


def _refused(email: CorpusEmail, sender: str, stderr: str) -> RehearsalSendError:
    if NO_MAIL_ACCOUNT_MARKER in stderr:
        return RehearsalSendError(
            f"Mail.app has no account for {sender}, so {email.id!r} was not sent.",
            next_action=(
                "Add the address to Mail.app, or set REHEARSAL_*_SENDER to an address Mail.app "
                "can send from."
            ),
        )
    return RehearsalSendError(
        f"Mail.app could not send {email.id!r} from {sender}: {stderr}",
        next_action=(
            "Check that Mail.app can send from that address, and that this terminal may control "
            "it (System Settings > Privacy & Security > Automation). The ids delivered before it "
            "are in the message."
        ),
    )
