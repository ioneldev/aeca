""".env.example is the file an evaluator copies to .env: it writes out the defaults, its values are
the ones the code uses when a setting is blank, and it holds no secret."""

from pathlib import Path

from aeca.settings import load_settings

ENV_EXAMPLE = Path(__file__).resolve().parents[1] / ".env.example"
SECRETS = ("GOOGLE_CLIENT_SECRET", "GOOGLE_REFRESH_TOKEN", "ANTHROPIC_API_KEY")
# The settings whose default is a value rather than "unset", so the example can write it out.
WRITTEN_DEFAULTS = (
    "WORK_HOURS",
    "MEETING_MINUTES",
    "BUFFER_MINUTES",
    "ALTERNATIVE_DAYS",
    "POLL_SECONDS",
    "SALES_DECLINE_NEEDS_APPROVAL",
    "MAX_USD_PER_DAY",
    "ALERT_AFTER_FAILURES",
    "DEBUG_LOG",
    "DEBUG_LOG_DAYS",
    "FORCE_LOCK",
)


def setting_lines() -> list[tuple[str, str]]:
    lines = ENV_EXAMPLE.read_text(encoding="utf-8").splitlines()
    settings = [line for line in lines if line.strip() and not line.lstrip().startswith("#")]
    return [_name_and_raw_value(line) for line in settings]


def _name_and_raw_value(line: str) -> tuple[str, str]:
    name, _, value = line.partition("=")
    return name.strip(), value


def example_settings() -> dict[str, str]:
    return {name: _unquoted(value) for name, value in setting_lines()}


def _unquoted(value: str) -> str:
    quoted = len(value) >= 2 and value[0] == value[-1] == '"'
    return value[1:-1] if quoted else value


def test_the_example_writes_out_every_default() -> None:
    example = example_settings()

    assert [name for name in WRITTEN_DEFAULTS if not example.get(name)] == []


def test_the_example_holds_a_blank_line_for_each_secret() -> None:
    example = example_settings()

    assert {name: example.get(name) for name in SECRETS} == dict.fromkeys(SECRETS, "")


def test_the_examples_values_are_the_codes_defaults(minimal_run_env: dict[str, str]) -> None:
    example = example_settings()
    filled = {**example, **{n: v for n, v in minimal_run_env.items() if not example.get(n)}}
    required_only = {name: filled[name] for name in minimal_run_env}

    assert load_settings(filled, purpose="run") == load_settings(required_only, purpose="run")


# uv, which runs the seed-calendar, rehearse and reset-test targets, refuses a value with an
# unquoted space and then drops the whole file, every other setting included (measured).
def test_a_value_with_a_space_is_quoted() -> None:
    unquoted = [
        name
        for name, value in setting_lines()
        if " " in value.strip() and _unquoted(value) == value
    ]

    assert unquoted == []
