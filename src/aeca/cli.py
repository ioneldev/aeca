import argparse
import os
import signal
import sys
import threading
from collections.abc import Callable, Sequence
from datetime import datetime
from pathlib import Path

from aeca.adapters.clock import SystemClock
from aeca.adapters.env_file import set_env_value
from aeca.adapters.google_auth import ConsentRequest, run_consent
from aeca.adapters.heartbeat_file import HEARTBEAT_FILE_NAME, read_heartbeat
from aeca.adapters.sqlite_state import StateNeedsMigration, read_awaiting_approvals
from aeca.domain.actions import Action
from aeca.domain.approval import Approval
from aeca.domain.errors import AecaError
from aeca.domain.lease import is_fresh, stale_after
from aeca.settings import load_settings
from aeca.tools.tool_commands import add_tool_commands
from aeca.wiring import AUDIT_FILE_NAME, STATE_FILE_NAME, build

REFRESH_TOKEN_VARIABLE = "GOOGLE_REFRESH_TOKEN"
DATA_DIR_VARIABLE = "AECA_DATA_DIR"
DEFAULT_DATA_DIR = "/data"
POLL_SECONDS_VARIABLE = "POLL_SECONDS"
DEFAULT_POLL_SECONDS = 30

# The seams the tests replace, so a test never builds the Google and Anthropic clients, and
# reads a fixed time.
build_service = build
clock = SystemClock()
MINUTES_PER_HOUR = 60
HOURS_PER_DAY = 24

Handler = Callable[[argparse.Namespace], int]


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    handler: Handler = arguments.handler
    try:
        return handler(arguments)
    except AecaError as error:
        print(f"error: {error}\n  next: {error.next_action}", file=sys.stderr)
        return 1


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="aeca", description="Agentic Email & Calendar Assistant")
    commands = parser.add_subparsers(dest="command", required=True)
    auth = commands.add_parser("auth", help="one-time Google consent; writes the refresh token")
    auth.add_argument("--env-file", type=Path, default=Path(".env"))
    auth.add_argument("--bind", default="127.0.0.1")
    auth.add_argument("--port", type=int, default=8765)
    auth.set_defaults(handler=_auth)
    run = commands.add_parser("run", help="watch the inbox until stopped")
    run.set_defaults(handler=_run)
    health = commands.add_parser("health", help="exit 0 when AECA polled recently (for Docker)")
    health.set_defaults(handler=_health)
    audit = commands.add_parser("audit", help="print the audit log")
    audit.set_defaults(handler=_audit)
    approvals = commands.add_parser(
        "approvals", help="list the approvals the manager has not answered"
    )
    approvals.set_defaults(handler=_approvals)
    add_tool_commands(commands)
    return parser


def _auth(arguments: argparse.Namespace) -> int:
    settings = load_settings(os.environ, purpose="auth")
    token = run_consent(
        ConsentRequest(
            client_id=settings.google_client_id,
            client_secret=settings.google_client_secret,
            bind_address=arguments.bind,
            port=arguments.port,
        )
    )
    set_env_value(arguments.env_file, REFRESH_TOKEN_VARIABLE, token.reveal())
    print(f"Done: the refresh token was written to {arguments.env_file}. It is not shown here.")
    return 0


def _run(_arguments: argparse.Namespace) -> int:
    settings = load_settings(os.environ, purpose="run")
    stop = threading.Event()
    _stop_on_signals(stop)
    service = build_service(settings)
    # A stop during startup is honoured here: run() would otherwise start its loop first.
    if not stop.is_set():
        service.run(stop)
    return 0


# A signal only sets the event: the email in hand is finished, and the loop then ends by itself.
def _stop_on_signals(stop: threading.Event) -> None:
    for number in (signal.SIGTERM, signal.SIGINT):
        signal.signal(number, lambda _number, _frame: stop.set())


# Reads the heartbeat file and POLL_SECONDS alone: Docker runs this every 30 s, and it must not
# need a credential, a network or the state file.
def _health(_arguments: argparse.Namespace) -> int:
    path = _data_dir() / HEARTBEAT_FILE_NAME
    written_at = read_heartbeat(path)
    if written_at is None:
        print(f"unhealthy: no readable heartbeat at {path}")
        return 1
    now = clock.now()
    limit = stale_after(_poll_seconds())
    if is_fresh(written_at, now, limit):
        return 0
    limit_seconds = int(limit.total_seconds())
    print(f"unhealthy: last poll at {written_at.isoformat()}, limit {limit_seconds} s")
    return 1


def _poll_seconds() -> float:
    try:
        return float(os.environ.get(POLL_SECONDS_VARIABLE) or DEFAULT_POLL_SECONDS)
    except ValueError:
        return DEFAULT_POLL_SECONDS


# Reads the data directory alone, so the audit can be shown without any credential.
def _audit(_arguments: argparse.Namespace) -> int:
    path = _data_dir() / AUDIT_FILE_NAME
    if not path.exists():
        print(f"No audit log yet: {path} does not exist.")
        return 0
    sys.stdout.write(path.read_text(encoding="utf-8"))
    return 0


# Reads the state file alone, like the audit, and never creates, migrates or writes it: AECA may be
# running beside it.
def _approvals(_arguments: argparse.Namespace) -> int:
    path = _data_dir() / STATE_FILE_NAME
    if not path.exists():
        print(f"No approvals yet: {path} does not exist.")
        return 0
    try:
        waiting = read_awaiting_approvals(path)
    except StateNeedsMigration:
        print("The state file is from an older AECA: start AECA once to migrate the state file.")
        return 0
    if not waiting:
        print("No approval is waiting.")
        return 0
    now = clock.now()
    for approval in waiting:
        print(_approval_line(approval, now))
    return 0


def _approval_line(approval: Approval, now: datetime) -> str:
    subject = approval.source_subject or "(subject not recorded)"
    actions = ", ".join(_action_name(action) for action in approval.actions)
    return f"{approval.id}  {_age(approval.opened_at, now)}  {subject}  {actions}"


def _action_name(action: Action) -> str:
    detail = action.args.get("template") or action.args.get("label")
    return f"{action.verb.value}:{detail}" if detail else action.verb.value


def _age(opened_at: datetime | None, now: datetime) -> str:
    if opened_at is None:
        return "unknown"
    total_minutes = int((now - opened_at).total_seconds() // 60)
    total_hours, minutes = divmod(total_minutes, MINUTES_PER_HOUR)
    days, hours = divmod(total_hours, HOURS_PER_DAY)
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def _data_dir() -> Path:
    return Path(os.environ.get(DATA_DIR_VARIABLE) or DEFAULT_DATA_DIR)
