import argparse
import os
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

from aeca.adapters.env_file import set_env_value
from aeca.adapters.google_auth import ConsentRequest, run_consent
from aeca.domain.errors import AecaError
from aeca.settings import load_settings
from aeca.tools.tool_commands import add_tool_commands

REFRESH_TOKEN_VARIABLE = "GOOGLE_REFRESH_TOKEN"

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
