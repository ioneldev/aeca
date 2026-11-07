import subprocess
from pathlib import Path

import pytest

from aeca import cli
from aeca.domain.errors import ConfigError
from aeca.tools import tool_commands
from aeca.tools.tool_commands import tool_data_dir


@pytest.fixture
def tools_environment(monkeypatch: pytest.MonkeyPatch, minimal_run_env: dict[str, str]) -> None:
    for name, value in minimal_run_env.items():
        monkeypatch.setenv(name, value)
    for name in (
        "REHEARSAL_INTERNAL_SENDER",
        "REHEARSAL_EXTERNAL_SENDER",
    ):
        monkeypatch.delenv(name, raising=False)


def test_rehearse_without_its_senders_names_the_missing_settings_and_runs_nothing(
    tools_environment: None, capsys: pytest.CaptureFixture[str]
) -> None:
    code = cli.main(["rehearse"])

    err = capsys.readouterr().err
    assert code == 1
    assert "REHEARSAL_INTERNAL_SENDER" in err and "REHEARSAL_EXTERNAL_SENDER" in err


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


REPO = Path(__file__).resolve().parents[2]


def test_the_data_dir_of_a_tool_is_the_local_data_folder_when_unset(
    minimal_run_env: dict[str, str],
) -> None:
    from aeca.settings import load_settings

    settings = load_settings(minimal_run_env, purpose="tools")

    assert tool_data_dir(minimal_run_env, settings) == Path("data")


def test_the_data_dir_of_a_tool_is_honoured_when_set(minimal_run_env: dict[str, str]) -> None:
    from aeca.settings import load_settings

    env = minimal_run_env | {"AECA_DATA_DIR": "/somewhere/else"}

    assert tool_data_dir(env, load_settings(env, purpose="tools")) == Path("/somewhere/else")


def test_eval_without_a_data_dir_writes_under_the_local_data_folder_and_never_touches_slash_data(
    tools_environment: None,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("AECA_DATA_DIR", raising=False)
    monkeypatch.chdir(tmp_path)
    made: list[Path] = []
    real_mkdir = Path.mkdir

    def recording_mkdir(self: Path, *args: object, **kwargs: object) -> None:
        made.append(self)
        real_mkdir(self, *args, **kwargs)  # type: ignore[arg-type]

    def stop_before_any_call(*_args: object, **_kwargs: object) -> None:
        raise ConfigError("stopped before the model", next_action="nothing to do")

    monkeypatch.setattr(Path, "mkdir", recording_mkdir)
    monkeypatch.setattr(tool_commands, "_decide_deps", stop_before_any_call)

    code = cli.main(["eval", "--corpus", str(REPO / "tests" / "corpus" / "emails.json")])

    assert code == 1 and "stopped before the model" in capsys.readouterr().err
    assert (tmp_path / "data").is_dir()
    assert Path("/data") not in made and not any(str(path).startswith("/data") for path in made)


@pytest.mark.parametrize("target", ["eval", "seed-calendar", "rehearse"])
def test_the_host_run_make_targets_set_the_local_data_dir(target: str) -> None:
    dry_run = subprocess.run(
        ["make", "-n", target], cwd=REPO, capture_output=True, text=True, check=True
    ).stdout

    assert f"AECA_DATA_DIR=data uv run --env-file .env aeca {target}" in dry_run


def test_a_week_start_that_is_not_a_monday_is_refused(
    tools_environment: None, capsys: pytest.CaptureFixture[str]
) -> None:
    code = cli.main(["seed-calendar", "--week-start", "2026-11-10"])

    assert code == 1 and "Monday" in capsys.readouterr().err


def test_rehearse_says_which_address_plays_which_role_before_sending(
    tools_environment: None,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("REHEARSAL_INTERNAL_SENDER", "in@mail.test")
    monkeypatch.setenv("REHEARSAL_EXTERNAL_SENDER", "out@mail.test")
    monkeypatch.setattr(tool_commands, "OsascriptRunner", _RefusingRunner)

    cli.main(["rehearse", "--corpus", str(tmp_path / "none.json")])

    out = capsys.readouterr().out
    assert "internal: in@mail.test" in out and "external: out@mail.test" in out


class _RefusingRunner:
    def run(self, script: str, arguments: object = ()) -> str:
        raise AssertionError("a test must never run osascript")
