class AecaError(Exception):
    """A failure the user can fix, so it always says what to do next."""

    def __init__(self, message: str, *, next_action: str) -> None:
        super().__init__(message)
        self.next_action = next_action


class ConfigError(AecaError):
    """A setting is missing or malformed, or an OAuth step produced nothing usable."""
