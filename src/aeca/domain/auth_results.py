"""Reads Gmail's own verdict on who sent an email, and nothing a sender wrote themselves."""

import re
from collections.abc import Sequence
from dataclasses import dataclass

GOOGLE_AUTHSERV_ID = "mx.google.com"
COMMENT = re.compile(r"\([^)]*\)")
RESULT = re.compile(r"^\s*(spf|dkim|dmarc)\s*=\s*([a-z]+)", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class AuthResults:
    spf: str | None
    dkim: str | None
    dmarc: str | None


def parse_authentication_results(values: Sequence[str]) -> AuthResults:
    """Only the first value from Gmail's server counts. Gmail adds its header above whatever the
    sender sent, so a forged result sits lower down and is never reached."""
    for value in values:
        if _authserv_id(value) == GOOGLE_AUTHSERV_ID:
            return _read_results(value)
    return AuthResults(spf=None, dkim=None, dmarc=None)


def _authserv_id(value: str) -> str:
    server_part = value.split(";", 1)[0].split()
    return server_part[0].lower() if server_part else ""


def _read_results(value: str) -> AuthResults:
    found: dict[str, str] = {}
    for segment in COMMENT.sub(" ", value).split(";")[1:]:
        match = RESULT.match(segment)
        if match:
            found.setdefault(match.group(1).lower(), match.group(2).lower())
    return AuthResults(spf=found.get("spf"), dkim=found.get("dkim"), dmarc=found.get("dmarc"))
