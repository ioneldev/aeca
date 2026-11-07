from decimal import Decimal
from pathlib import Path

import pytest

from aeca.domain.errors import AecaError, ConfigError
from aeca.settings import load_settings, require_rehearsal_senders


def test_a_missing_variable_is_named() -> None:
    with pytest.raises(ConfigError, match="GMAIL_ADDRESS"):
        load_settings({}, purpose="run")


def test_a_config_error_says_what_to_do_next() -> None:
    with pytest.raises(ConfigError) as raised:
        load_settings({}, purpose="run")

    assert isinstance(raised.value, AecaError)
    assert ".env" in raised.value.next_action


def test_defaults(minimal_run_env: dict[str, str]) -> None:
    s = load_settings(minimal_run_env, purpose="run")
    assert (s.poll_seconds, s.meeting_minutes, s.buffer_minutes, s.alternative_days) == (
        30,
        30,
        10,
        5,
    )
    assert s.work_hours == "Mon-Fri 09:00-17:00" and s.max_usd_per_day == Decimal("5")
    assert s.alert_after_failures == 3 and s.debug_log is False and s.debug_log_days == 7
    assert s.manager_address is None and s.internal_senders == frozenset()
    assert s.bulk_senders == frozenset() and s.timezone is None and s.extra_ca_certs is None
    assert s.force_lock is False and s.sales_decline_needs_approval is False
    assert s.data_dir == Path("/data")


def test_internal_senders_are_trimmed_and_lowercased(minimal_run_env: dict[str, str]) -> None:
    s = load_settings(
        {**minimal_run_env, "INTERNAL_SENDERS": "Boss@Company.COM, a@b.c "}, purpose="run"
    )
    assert s.internal_senders == {"boss@company.com", "a@b.c"}


def test_bulk_senders_are_trimmed_and_lowercased_and_skip_empty_entries(
    minimal_run_env: dict[str, str],
) -> None:
    s = load_settings({**minimal_run_env, "BULK_SENDERS": " News.COM,, a@B.c,"}, purpose="run")
    assert s.bulk_senders == {"news.com", "a@b.c"}


def test_a_bad_integer_names_its_variable(minimal_run_env: dict[str, str]) -> None:
    with pytest.raises(ConfigError, match="POLL_SECONDS"):
        load_settings({**minimal_run_env, "POLL_SECONDS": "abc"}, purpose="run")


def test_a_bad_decimal_names_its_variable(minimal_run_env: dict[str, str]) -> None:
    with pytest.raises(ConfigError, match="MAX_USD_PER_DAY"):
        load_settings({**minimal_run_env, "MAX_USD_PER_DAY": "five"}, purpose="run")


@pytest.mark.parametrize("text", ["true", "TRUE", "1", "yes", "Yes"])
def test_booleans_accept_the_true_spellings(minimal_run_env: dict[str, str], text: str) -> None:
    assert load_settings({**minimal_run_env, "DEBUG_LOG": text}, purpose="run").debug_log is True


@pytest.mark.parametrize("text", ["false", "False", "0", "no", "NO"])
def test_booleans_accept_the_false_spellings(minimal_run_env: dict[str, str], text: str) -> None:
    assert load_settings({**minimal_run_env, "FORCE_LOCK": text}, purpose="run").force_lock is False


def test_a_bad_boolean_names_its_variable(minimal_run_env: dict[str, str]) -> None:
    with pytest.raises(ConfigError, match="FORCE_LOCK"):
        load_settings({**minimal_run_env, "FORCE_LOCK": "maybe"}, purpose="run")


def test_empty_strings_count_as_unset(minimal_run_env: dict[str, str]) -> None:
    s = load_settings(
        {**minimal_run_env, "POLL_SECONDS": "", "MANAGER_ADDRESS": "", "DEBUG_LOG": " "},
        purpose="run",
    )
    assert (s.poll_seconds, s.manager_address, s.debug_log) == (30, None, False)


def test_an_empty_required_variable_is_missing(minimal_run_env: dict[str, str]) -> None:
    with pytest.raises(ConfigError, match="ANTHROPIC_API_KEY"):
        load_settings({**minimal_run_env, "ANTHROPIC_API_KEY": ""}, purpose="run")


def test_auth_needs_no_anthropic_key(minimal_auth_env: dict[str, str]) -> None:
    s = load_settings(minimal_auth_env, purpose="auth")
    assert s.anthropic_api_key is None and s.google_refresh_token is None


@pytest.mark.parametrize("purpose", ["run", "tools"])
def test_run_and_tools_need_the_token_and_the_key(
    minimal_auth_env: dict[str, str], purpose: str
) -> None:
    with pytest.raises(ConfigError, match="GOOGLE_REFRESH_TOKEN"):
        load_settings(minimal_auth_env, purpose=purpose)  # type: ignore[arg-type]


def test_settings_repr_shows_no_secret(minimal_run_env: dict[str, str]) -> None:
    shown = repr(load_settings(minimal_run_env, purpose="run"))
    assert minimal_run_env["ANTHROPIC_API_KEY"] not in shown
    assert minimal_run_env["GOOGLE_CLIENT_SECRET"] not in shown
    assert minimal_run_env["GOOGLE_REFRESH_TOKEN"] not in shown


def test_rehearsal_settings_are_optional(minimal_run_env: dict[str, str]) -> None:
    s = load_settings(minimal_run_env, purpose="tools")

    assert s.rehearsal_internal_sender is None and s.rehearsal_external_sender is None


def test_rehearsal_senders_are_plain_addresses(minimal_run_env: dict[str, str]) -> None:
    env = minimal_run_env | {
        "REHEARSAL_INTERNAL_SENDER": "in@mail.test",
        "REHEARSAL_EXTERNAL_SENDER": "out@mail.test",
    }

    senders = require_rehearsal_senders(load_settings(env, purpose="tools"))

    assert (senders.internal, senders.external) == ("in@mail.test", "out@mail.test")


def test_missing_rehearsal_senders_are_all_named(minimal_run_env: dict[str, str]) -> None:
    env = minimal_run_env | {"REHEARSAL_INTERNAL_SENDER": "in@mail.test"}

    with pytest.raises(ConfigError) as raised:
        require_rehearsal_senders(load_settings(env, purpose="tools"))

    message = str(raised.value)
    assert "REHEARSAL_EXTERNAL_SENDER" in message
    assert "REHEARSAL_INTERNAL_SENDER" not in message
    assert ".env" in raised.value.next_action


def test_requests_and_notices_go_to_manager_address_when_set_else_to_the_account(
    minimal_run_env: dict[str, str],
) -> None:
    unset = load_settings({**minimal_run_env, "MANAGER_ADDRESS": ""}, purpose="run")
    chosen = load_settings(
        {**minimal_run_env, "MANAGER_ADDRESS": "boss@example.com"}, purpose="run"
    )

    assert unset.manager_or_account == unset.gmail_address
    assert chosen.manager_or_account == "boss@example.com"
