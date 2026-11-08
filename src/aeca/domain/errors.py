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


class LockLost(AecaError):
    """Another copy took the inbox lock, or this one slept past its own heartbeat."""

    @classmethod
    def for_a_lost_lock(cls) -> "LockLost":
        return cls(
            "This copy of AECA no longer holds the inbox lock: another copy took it, "
            "or this machine slept past the heartbeat.",
            next_action="Stop the other copy, then start this one again; "
            "set FORCE_LOCK=true to take the lock back",
        )
