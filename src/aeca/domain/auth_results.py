"""Reads Gmail's own verdict on who sent an email, and nothing a sender wrote themselves."""

import re
from collections.abc import Sequence
from dataclasses import dataclass

GOOGLE_AUTHSERV_ID = "mx.google.com"
RESULT = re.compile(r"^\s*(spf|dkim|dmarc)\s*=\s*([a-z]+)", re.IGNORECASE)
HEADER_FROM = re.compile(r"\sheader\.from\s*=\s*([^\s;]+)", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class AuthResults:
    spf: str | None
    dkim: str | None
    dmarc: str | None
    # The From domain Gmail judged DMARC for. A pass proves that domain and no other, so a
    # caller matches it against the sender it reads; None means nothing can be matched.
    dmarc_domain: str | None = None


def parse_authentication_results(values: Sequence[str]) -> AuthResults:
    """Only the first value from Gmail's server counts. Gmail adds its header above whatever the
    sender sent, so a forged result sits lower down and is never reached."""
    for value in values:
        if _authserv_id(value) == GOOGLE_AUTHSERV_ID:
            return _read_results(value)
    return AuthResults(spf=None, dkim=None, dmarc=None)


@dataclass(frozen=True, slots=True)
class _Tokens:
    segments: list[str]
    well_formed: bool


def _authserv_id(value: str) -> str:
    server_part = _tokenise(value).segments[0].split()
    return server_part[0].lower() if server_part else ""


# Gmail writes one dkim result per signature but exactly one dmarc result, so a second dmarc result
# means the header was tampered with or is not what we think it is.
def _read_results(value: str) -> AuthResults:
    tokens = _tokenise(value)
    found: dict[str, str] = {}
    dmarc_segments: list[str] = []
    for segment in tokens.segments[1:]:
        match = RESULT.match(segment)
        if match:
            found.setdefault(match.group(1).lower(), match.group(2).lower())
            if match.group(1).lower() == "dmarc":
                dmarc_segments.append(segment)
    trusted = tokens.well_formed and len(dmarc_segments) <= 1
    if not trusted:
        found["dmarc"] = "fail"
    return AuthResults(
        spf=found.get("spf"),
        dkim=found.get("dkim"),
        dmarc=found.get("dmarc"),
        dmarc_domain=_header_from(dmarc_segments[0]) if trusted and dmarc_segments else None,
    )


def _header_from(dmarc_segment: str) -> str | None:
    match = HEADER_FROM.search(dmarc_segment)
    return match.group(1).lower() if match else None


def _tokenise(value: str) -> _Tokens:
    """Splits on `;` outside quoted strings and comments (RFC 8601), with comments removed. A
    quoted local part such as "a;dmarc=pass"@x must not start a result of its own. Anything the
    grammar doesn't allow makes the header untrustworthy, because a reader that disagrees with
    Gmail's about where a result starts can be made to read a forged one."""
    segments: list[str] = [""]
    comment_depth = 0
    in_quotes = False
    escaped = False
    well_formed = True
    for character in value:
        if escaped:
            escaped = False
            if comment_depth == 0:
                segments[-1] += character
        elif character == "\\":
            well_formed = well_formed and (in_quotes or comment_depth > 0)
            escaped = in_quotes or comment_depth > 0
            if comment_depth == 0:
                segments[-1] += character
        elif in_quotes:
            in_quotes = character != '"'
            segments[-1] += character
        elif comment_depth:
            comment_depth += {"(": 1, ")": -1}.get(character, 0)
            if comment_depth == 0:
                segments[-1] += " "
        elif character == "(":
            comment_depth = 1
        elif character == ")":
            well_formed = False
        elif character == ";":
            segments.append("")
        else:
            in_quotes = character == '"'
            segments[-1] += character
    return _Tokens(segments, well_formed and not in_quotes and comment_depth == 0 and not escaped)
