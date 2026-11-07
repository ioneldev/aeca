import argparse
import os
import signal
import sys
import threading
from collections.abc import Callable, Sequence
from pathlib import Path

from aeca.adapters.env_file import set_env_value
from aeca.adapters.google_auth import ConsentRequest, run_consent
from aeca.domain.errors import AecaError
from aeca.settings import load_settings
from aeca.wiring import AUDIT_FILE_NAME, build

REFRESH_TOKEN_VARIABLE = "GOOGLE_REFRESH_TOKEN"
DATA_DIR_VARIABLE = "AECA_DATA_DIR"
DEFAULT_DATA_DIR = "/data"

# The seam the tests replace, so a test never builds the Google and Anthropic clients.
build_service = build

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
    audit = commands.add_parser("audit", help="print the audit log")
    audit.set_defaults(handler=_audit)
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


# Reads the data directory alone, so the audit can be shown without any credential.
def _audit(_arguments: argparse.Namespace) -> int:
    path = Path(os.environ.get(DATA_DIR_VARIABLE) or DEFAULT_DATA_DIR) / AUDIT_FILE_NAME
    if not path.exists():
        print(f"No audit log yet: {path} does not exist.")
        return 0
    sys.stdout.write(path.read_text(encoding="utf-8"))
    return 0
