from pathlib import Path

import pytest

from aeca import cli
from aeca.domain.errors import ConfigError
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
