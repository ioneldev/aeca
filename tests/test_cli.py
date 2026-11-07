import os
import signal
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest

from aeca import cli
from aeca.domain.errors import ConfigError, CredentialsRevoked
from aeca.domain.secret import SecretStr


@pytest.fixture
def auth_environment(monkeypatch: pytest.MonkeyPatch, minimal_auth_env: dict[str, str]) -> None:
    for name, value in minimal_auth_env.items():
        monkeypatch.setenv(name, value)


def test_auth_writes_the_token_to_the_env_file_and_never_prints_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    auth_environment: None,
) -> None:
    env = tmp_path / ".env"
    env.write_text("GMAIL_ADDRESS=a@b.c\n")
    monkeypatch.setattr(cli, "run_consent", lambda _request: SecretStr("1//the-secret-token"))

    code = cli.main(["auth", "--env-file", str(env), "--bind", "127.0.0.1", "--port", "8765"])

    shown = capsys.readouterr()
    assert code == 0
    assert "GOOGLE_REFRESH_TOKEN=1//the-secret-token" in env.read_text()
    assert "1//the-secret-token" not in shown.out + shown.err
    assert str(env) in shown.out


def test_a_config_error_prints_its_message_and_next_action_and_exits_non_zero(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    auth_environment: None,
) -> None:
    def refuse(_request: object) -> SecretStr:
        raise ConfigError("no token came back", next_action="revoke access and re-run")

    monkeypatch.setattr(cli, "run_consent", refuse)

    code = cli.main(["auth", "--env-file", str(tmp_path / ".env")])

    err = capsys.readouterr().err
    assert code == 1
    assert "no token came back" in err and "revoke access and re-run" in err


def test_auth_with_missing_settings_names_the_variable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    for name in ("GMAIL_ADDRESS", "GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET"):
        monkeypatch.delenv(name, raising=False)

    code = cli.main(["auth", "--env-file", str(tmp_path / ".env")])

    assert code == 1
    assert "GMAIL_ADDRESS" in capsys.readouterr().err


class FakeService:
    def __init__(self) -> None:
        self.ran_with: threading.Event | None = None
        self.handler_seen_during_run: object = None

    def run(self, stop: threading.Event) -> None:
        self.ran_with = stop
        os.kill(os.getpid(), signal.SIGTERM)
        self.handler_seen_during_run = stop.is_set()


@pytest.fixture
def run_environment(monkeypatch: pytest.MonkeyPatch, minimal_run_env: dict[str, str]) -> None:
    for name, value in minimal_run_env.items():
        monkeypatch.setenv(name, value)


@pytest.fixture
def restored_signal_handlers() -> Iterator[None]:
    saved = {number: signal.getsignal(number) for number in (signal.SIGTERM, signal.SIGINT)}
    yield
    for number, handler in saved.items():
        signal.signal(number, handler)


def test_run_exits_zero_and_a_sigterm_sets_the_stop_event_instead_of_killing_the_process(
    monkeypatch: pytest.MonkeyPatch, run_environment: None, restored_signal_handlers: None
) -> None:
    service = FakeService()
    monkeypatch.setattr(cli, "build_service", lambda _settings: service)

    code = cli.main(["run"])

    assert code == 0
    assert service.handler_seen_during_run is True


def test_run_with_a_missing_setting_exits_non_zero_names_it_and_builds_nothing(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    run_environment: None,
    restored_signal_handlers: None,
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    built: list[object] = []
    monkeypatch.setattr(cli, "build_service", lambda settings: built.append(settings))

    code = cli.main(["run"])

    assert code == 1
    assert "ANTHROPIC_API_KEY" in capsys.readouterr().err
    assert built == []


def test_run_with_revoked_credentials_exits_non_zero_with_the_next_action(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    run_environment: None,
    restored_signal_handlers: None,
) -> None:
    def revoked(_settings: object) -> None:
        raise CredentialsRevoked("Google refused the token", next_action="Run make auth")

    monkeypatch.setattr(cli, "build_service", revoked)

    code = cli.main(["run"])

    err = capsys.readouterr().err
    assert code == 1
    assert "Google refused the token" in err and "Run make auth" in err


def test_audit_prints_the_audit_file_and_needs_no_credentials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    for name in ("GMAIL_ADDRESS", "GOOGLE_CLIENT_ID", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("AECA_DATA_DIR", str(tmp_path))
    (tmp_path / "audit.jsonl").write_text('{"outcome":"done"}\n{"outcome":"failed"}\n')

    code = cli.main(["audit"])

    assert code == 0
    assert capsys.readouterr().out == '{"outcome":"done"}\n{"outcome":"failed"}\n'


def test_audit_with_no_file_says_so_in_one_line_and_exits_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("AECA_DATA_DIR", str(tmp_path))

    code = cli.main(["audit"])

    out = capsys.readouterr().out
    assert code == 0
    assert out.count("\n") == 1
    assert "audit.jsonl" in out


def test_audit_reads_the_data_volume_when_the_directory_is_not_set(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("AECA_DATA_DIR", raising=False)
    monkeypatch.setattr(cli, "AUDIT_FILE_NAME", "no-such-file-here.jsonl")

    assert cli.main(["audit"]) == 0
    assert "/data/no-such-file-here.jsonl" in capsys.readouterr().out
