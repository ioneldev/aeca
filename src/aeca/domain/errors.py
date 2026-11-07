class AecaError(Exception):
    """A failure the user can fix, so it always says what to do next."""

    def __init__(self, message: str, *, next_action: str) -> None:
        super().__init__(message)
        self.next_action = next_action


class ConfigError(AecaError):
    """A setting is missing or malformed, or an OAuth step produced nothing usable."""


class SystemWideError(AecaError):
    """Every email would fail the same way, so the whole tick stops and AECA backs off."""


class CredentialsRevoked(SystemWideError):
    """Google no longer honours the stored token."""


class EmailError(AecaError):
    """The failure belongs to one email, so the others carry on."""
