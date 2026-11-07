from pathlib import Path

import pytest

from aeca import cli


@pytest.fixture
def tools_environment(monkeypatch: pytest.MonkeyPatch, minimal_run_env: dict[str, str]) -> None:
    for name, value in minimal_run_env.items():
        monkeypatch.setenv(name, value)
    for name in (
        "REHEARSAL_INTERNAL_SENDER",
        "REHEARSAL_INTERNAL_APP_PASSWORD",
        "REHEARSAL_EXTERNAL_SENDER",
        "REHEARSAL_EXTERNAL_APP_PASSWORD",
    ):
        monkeypatch.delenv(name, raising=False)


def test_rehearse_without_its_accounts_names_the_missing_settings_and_connects_nowhere(
    tools_environment: None, capsys: pytest.CaptureFixture[str]
) -> None:
    code = cli.main(["rehearse"])

    err = capsys.readouterr().err
    assert code == 1
    assert "REHEARSAL_INTERNAL_SENDER" in err and "REHEARSAL_EXTERNAL_APP_PASSWORD" in err


def test_eval_with_an_unreadable_corpus_says_so(
    tools_environment: None, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = cli.main(["eval", "--corpus", str(tmp_path / "nope.json")])

    assert code == 1
    assert "nope.json" in capsys.readouterr().err


@pytest.mark.parametrize("command", ["eval", "seed-calendar", "rehearse"])
def test_each_tool_command_is_registered(command: str, capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as raised:
        cli.main([command, "--help"])

    assert raised.value.code == 0
    assert command in capsys.readouterr().out
