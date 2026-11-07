from collections.abc import Callable, Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Literal

from aeca.domain.errors import ConfigError
from aeca.domain.secret import SecretStr

Purpose = Literal["run", "auth", "tools"]

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
        timezone=reader.text("TIMEZONE"),
        work_hours=reader.text("WORK_HOURS") or "Mon-Fri 09:00-17:00",
        meeting_minutes=reader.integer("MEETING_MINUTES", default=30),
        buffer_minutes=reader.integer("BUFFER_MINUTES", default=10),
        alternative_days=reader.integer("ALTERNATIVE_DAYS", default=5),
        poll_seconds=reader.integer("POLL_SECONDS", default=30),
        sales_decline_needs_approval=reader.boolean("SALES_DECLINE_NEEDS_APPROVAL"),
        max_usd_per_day=reader.decimal("MAX_USD_PER_DAY", default=Decimal(5)),
        alert_after_failures=reader.integer("ALERT_AFTER_FAILURES", default=3),
        debug_log=reader.boolean("DEBUG_LOG"),
        debug_log_days=reader.integer("DEBUG_LOG_DAYS", default=7),
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

    def integer(self, name: str, *, default: int) -> int:
        return self._parsed(name, int, default, expected="a whole number")

    def decimal(self, name: str, *, default: Decimal) -> Decimal:
        return self._parsed(name, Decimal, default, expected="a number")

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


def _malformed(name: str, *, expected: str) -> ConfigError:
    return ConfigError(
        f"{name} is not valid: it must be {expected}.",
        next_action=f"Fix {name} in .env (see .env.example), then run again.",
    )
