"""Keeps AECA's secrets out of whatever pytest prints, whichever test goes wrong.

The secrets leave the environment before any test runs, tests get the Anthropic key masked in a
SecretStr, and every report is scrubbed of any stretch of any secret, so a truncated repr is caught
too.

pytest -s prints straight to the terminal, past these reports, so never use it with a secret set.
"""

import os
import re
from collections.abc import Callable, Mapping

import pytest

from aeca.domain.secret import SecretStr

SECRET_VARIABLES = ("ANTHROPIC_API_KEY", "GOOGLE_CLIENT_SECRET", "GOOGLE_REFRESH_TOKEN")
REDACTED = "[REDACTED]"
# Shorter than any fragment worth having, and longer than the public prefix sk-ant-api03-.
FRAGMENT_LENGTH = 16
GUARD = pytest.StashKey["SecretGuard"]()


class SecretGuard:
    """Holds the secrets masked, and their fragments only in a compiled pattern, for no repr to
    print."""

    def __init__(self, secrets: Mapping[str, SecretStr]) -> None:
        self.secrets = secrets
        self._fragments = _fragment_pattern(list(secrets.values())) if secrets else None

    def holds_secret(self, text: str) -> bool:
        return self._fragments is not None and self._fragments.search(text) is not None

    def scrub(self, text: str) -> str:
        return self._fragments.sub(REDACTED, text) if self._fragments else text

    # tryfirst, because the reporters that write files read a report as soon as they are called.
    @pytest.hookimpl(tryfirst=True)
    def pytest_runtest_logreport(self, report: pytest.TestReport) -> None:
        if self._fragments is None:
            return
        report.longrepr = self._scrubbed_failure(report.longrepr)
        report.sections = [(name, self.scrub(text)) for name, text in report.sections]

    # A skip is a tuple, which pytest folds by position, so it has to stay one.
    def _scrubbed_failure(self, longrepr: object) -> object:
        if isinstance(longrepr, tuple):
            return tuple(self.scrub(part) if isinstance(part, str) else part for part in longrepr)
        return None if longrepr is None else self.scrub(str(longrepr))


# Built in one expression, so no frame holds a plain fragment for --showlocals to print.
def _fragment_pattern(secrets: list[SecretStr]) -> re.Pattern[str]:
    return re.compile(
        "(?:"
        + "|".join(
            re.escape(secret.reveal()[start : start + min(FRAGMENT_LENGTH, len(secret))])
            for secret in secrets
            for start in range(len(secret) - min(FRAGMENT_LENGTH, len(secret)) + 1)
        )
        + r")+[\w-]*"
    )


def pytest_configure(config: pytest.Config) -> None:
    taken = {name: os.environ.pop(name, "") for name in SECRET_VARIABLES}
    guard = SecretGuard({name: SecretStr(value) for name, value in taken.items() if value})
    config.stash[GUARD] = guard
    config.pluginmanager.register(guard, "secret-guard")


@pytest.fixture
def api_key(request: pytest.FixtureRequest) -> SecretStr:
    key = request.config.stash[GUARD].secrets.get("ANTHROPIC_API_KEY")
    if key is None:
        pytest.fail("ANTHROPIC_API_KEY is not set: run these tests with make eval, not pytest")
    return key


@pytest.fixture
def holds_secret(request: pytest.FixtureRequest) -> Callable[[str], bool]:
    return request.config.stash[GUARD].holds_secret


# Plainly fake, and distinct, so a test can tell which one leaked.
@pytest.fixture
def minimal_auth_env() -> dict[str, str]:
    return {
        "GMAIL_ADDRESS": "aeca@example.com",
        "GOOGLE_CLIENT_ID": "fake-client-id.apps.googleusercontent.com",
        "GOOGLE_CLIENT_SECRET": "fake-client-secret-0123456789",
    }


@pytest.fixture
def minimal_run_env(minimal_auth_env: dict[str, str]) -> dict[str, str]:
    return {
        **minimal_auth_env,
        "GOOGLE_REFRESH_TOKEN": "fake-refresh-token-0123456789",
        "ANTHROPIC_API_KEY": "sk-ant-fake-0123456789-not-a-real-key",
    }
