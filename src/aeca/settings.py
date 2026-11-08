from collections.abc import Callable, Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from aeca.domain.errors import ConfigError
from aeca.domain.planner import parse_work_hours
from aeca.domain.secret import SecretStr

Purpose = Literal["run", "auth", "tools"]
IntegerRange = tuple[int | None, int | None]
DEFAULT_WORK_HOURS = "Mon-Fri 09:00-17:00"

# The edges of what AECA can run sensibly with.
MIN_POLL_SECONDS = 5
MEETING_MINUTES_RANGE = (5, 480)
BUFFER_MINUTES_RANGE = (0, 120)
ALTERNATIVE_DAYS_RANGE = (1, 30)

TRUE_SPELLINGS = frozenset({"true", "1", "yes"})
FALSE_SPELLINGS = frozenset({"false", "0", "no"})


@dataclass(frozen=True)
class Settings:
    gmail_address: str
    google_client_id: str
    google_client_secret: SecretStr
    google_refresh_token: SecretStr | None
    anthropic_api_key: SecretStr | None
    manager_address: str | None
    internal_senders: frozenset[str]
    bulk_senders: frozenset[str]
    timezone: str | None
    work_hours: str
    meeting_minutes: int
    buffer_minutes: int
    alternative_days: int
    poll_seconds: int
    sales_decline_needs_approval: bool
    max_usd_per_day: Decimal
    alert_after_failures: int
    debug_log: bool
    debug_log_days: int
    extra_ca_certs: Path | None
    force_lock: bool
    data_dir: Path
    rehearsal_internal_sender: str | None
    rehearsal_external_sender: str | None

    @property
    def manager_or_account(self) -> str:
        """Where approval requests, urgent notices and alerts go (spec §4.5)."""
        return self.manager_address or self.gmail_address


@dataclass(frozen=True)
class RehearsalSenders:
    """Addresses that Mail.app can send from: the internal colleague and the outside sender."""

    internal: str
    external: str


def load_settings(environ: Mapping[str, str], *, purpose: Purpose) -> Settings:
    """Reads every setting from environ, and raises ConfigError naming the variable at fault."""
    reader = _EnvironmentReader(environ)
    needs_run_secrets = purpose != "auth"
    return Settings(
        gmail_address=reader.required("GMAIL_ADDRESS"),
        google_client_id=reader.required("GOOGLE_CLIENT_ID"),
        google_client_secret=reader.required_secret("GOOGLE_CLIENT_SECRET"),
        google_refresh_token=reader.secret("GOOGLE_REFRESH_TOKEN", required=needs_run_secrets),
        anthropic_api_key=reader.secret("ANTHROPIC_API_KEY", required=needs_run_secrets),
        manager_address=reader.text("MANAGER_ADDRESS"),
        internal_senders=reader.lowercased_set("INTERNAL_SENDERS"),
        bulk_senders=reader.lowercased_set("BULK_SENDERS"),
        timezone=reader.timezone("TIMEZONE"),
        work_hours=reader.work_hours("WORK_HOURS"),
        meeting_minutes=reader.integer("MEETING_MINUTES", default=30, within=MEETING_MINUTES_RANGE),
        buffer_minutes=reader.integer("BUFFER_MINUTES", default=10, within=BUFFER_MINUTES_RANGE),
        alternative_days=reader.integer(
            "ALTERNATIVE_DAYS", default=5, within=ALTERNATIVE_DAYS_RANGE
        ),
        poll_seconds=reader.integer("POLL_SECONDS", default=30, within=(MIN_POLL_SECONDS, None)),
        sales_decline_needs_approval=reader.boolean("SALES_DECLINE_NEEDS_APPROVAL"),
        max_usd_per_day=reader.positive_decimal("MAX_USD_PER_DAY", default=Decimal(5)),
        alert_after_failures=reader.integer("ALERT_AFTER_FAILURES", default=3, within=(1, None)),
        debug_log=reader.boolean("DEBUG_LOG"),
        debug_log_days=reader.integer("DEBUG_LOG_DAYS", default=7, within=(1, None)),
        extra_ca_certs=reader.path("EXTRA_CA_CERTS"),
        force_lock=reader.boolean("FORCE_LOCK"),
        data_dir=reader.path("AECA_DATA_DIR") or Path("/data"),
        rehearsal_internal_sender=reader.text("REHEARSAL_INTERNAL_SENDER"),
        rehearsal_external_sender=reader.text("REHEARSAL_EXTERNAL_SENDER"),
    )


def require_rehearsal_senders(settings: Settings) -> RehearsalSenders:
    """Only `rehearse` needs the two sender addresses, so only it asks for them."""
    if settings.rehearsal_internal_sender is None or settings.rehearsal_external_sender is None:
        raise _missing_rehearsal_settings(settings)
    return RehearsalSenders(
        internal=settings.rehearsal_internal_sender, external=settings.rehearsal_external_sender
    )


def _missing_rehearsal_settings(settings: Settings) -> ConfigError:
    values = {
        "REHEARSAL_INTERNAL_SENDER": settings.rehearsal_internal_sender,
        "REHEARSAL_EXTERNAL_SENDER": settings.rehearsal_external_sender,
    }
    missing = ", ".join(name for name, value in values.items() if value is None)
    return ConfigError(
        f"make rehearse needs these settings, and they are not set: {missing}.",
        next_action="Set them in .env (see .env.example) to addresses Mail.app can send from, "
        "then run again.",
    )


class _EnvironmentReader:
    """Reads typed values out of an environment, where an empty string counts as unset."""

    def __init__(self, environ: Mapping[str, str]) -> None:
        self._environ = environ

    def text(self, name: str) -> str | None:
        return self._environ.get(name, "").strip() or None

    def required(self, name: str) -> str:
        value = self.text(name)
        if value is None:
            raise ConfigError(
                f"{name} is not set.",
                next_action=f"Set {name} in .env (see .env.example), then run again.",
            )
        return value

    def required_secret(self, name: str) -> SecretStr:
        return SecretStr(self.required(name))

    def secret(self, name: str, *, required: bool) -> SecretStr | None:
        value = self.required(name) if required else self.text(name)
        return None if value is None else SecretStr(value)

    def path(self, name: str) -> Path | None:
        value = self.text(name)
        return None if value is None else Path(value)

    def lowercased_set(self, name: str) -> frozenset[str]:
        entries = (self.text(name) or "").split(",")
        return frozenset(entry.strip().lower() for entry in entries if entry.strip())

    def integer(self, name: str, *, default: int, within: IntegerRange = (None, None)) -> int:
        value = self._parsed(name, int, default, expected="a whole number")
        low, high = within
        if (low is not None and value < low) or (high is not None and value > high):
            raise _malformed(name, expected=_range_in_words(low, high))
        return value

    def positive_decimal(self, name: str, *, default: Decimal) -> Decimal:
        value = self._parsed(name, Decimal, default, expected="a number")
        if not value.is_finite() or value <= 0:
            raise _malformed(name, expected="a number above zero")
        return value

    def work_hours(self, name: str) -> str:
        value = self.text(name) or DEFAULT_WORK_HOURS
        try:
            parse_work_hours(value)
        except ValueError as error:
            raise _malformed(name, expected=f"like {DEFAULT_WORK_HOURS} ({error})") from None
        return value

    def timezone(self, name: str) -> str | None:
        value = self.text(name)
        if value is None:
            return None
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError):
            raise _malformed(name, expected="an IANA name such as Europe/Bucharest") from None
        return value

    def boolean(self, name: str) -> bool:
        value = self.text(name)
        if value is None or value.lower() in FALSE_SPELLINGS:
            return False
        if value.lower() in TRUE_SPELLINGS:
            return True
        raise _malformed(name, expected="true or false (also 1, 0, yes, no)")

    def _parsed[T](self, name: str, parse: Callable[[str], T], default: T, *, expected: str) -> T:
        value = self.text(name)
        if value is None:
            return default
        try:
            return parse(value)
        except (ValueError, InvalidOperation):
            raise _malformed(name, expected=expected) from None


def _range_in_words(low: int | None, high: int | None) -> str:
    if high is None:
        return f"a whole number of at least {low}"
    return f"a whole number from {low} to {high}"


def _malformed(name: str, *, expected: str) -> ConfigError:
    return ConfigError(
        f"{name} is not valid: it must be {expected}.",
        next_action=f"Fix {name} in .env (see .env.example), then run again.",
    )
