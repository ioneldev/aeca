import os
import signal
import sqlite3
import threading
from collections.abc import Iterator
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import pytest
from fakes.fixed_clock import FIXED_NOW, FixedClock

from aeca import cli
from aeca.adapters.sqlite_state import SqliteState
from aeca.domain.actions import Action, Verb
from aeca.domain.approval import Approval, ApprovalState
from aeca.domain.errors import ConfigError, CredentialsRevoked, LockLost
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


def test_a_sigterm_during_startup_exits_zero_without_starting_the_loop(
    monkeypatch: pytest.MonkeyPatch, run_environment: None, restored_signal_handlers: None
) -> None:
    service = FakeService()

    def build_while_terminated(_settings: object) -> FakeService:
        os.kill(os.getpid(), signal.SIGTERM)
        return service

    monkeypatch.setattr(cli, "build_service", build_while_terminated)

    assert cli.main(["run"]) == 0
    assert service.ran_with is None


# --- aeca approvals: what waits for the manager, read from the state file alone ---


def awaiting_approval(approval_id: str, **overrides: object) -> Approval:
    approval = Approval(
        id=approval_id,
        source_message_id=f"m-{approval_id}",
        source_thread_id=f"t-{approval_id}",
        attempt=0,
        actions=(
            Action(verb=Verb.CREATE_EVENT, key=f"k-event-{approval_id}", args={"title": "Sync"}),
            Action(
                verb=Verb.SEND_REPLY,
                key=f"k-reply-{approval_id}",
                args={"template": "confirmation"},
            ),
        ),
        state=ApprovalState.AWAITING,
        request_thread_id=f"r-{approval_id}",
        summary="Wants a sync.",
        holds=(),
        opened_at=FIXED_NOW - timedelta(hours=2, minutes=5),
        source_subject="Sync on Tuesday?",
    )
    return replace(approval, **overrides)  # type: ignore[arg-type]


@pytest.fixture
def approvals_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    for name in ("GMAIL_ADDRESS", "GOOGLE_CLIENT_ID", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("AECA_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(cli, "clock", FixedClock())
    return tmp_path


def test_approvals_lists_each_awaiting_approval_and_needs_no_credentials(
    approvals_environment: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    store = SqliteState(approvals_environment / "state.db")
    store.approval_save(awaiting_approval("a-1"))
    store.approval_save(awaiting_approval("a-2", state=ApprovalState.REJECTED))
    store.approval_save(awaiting_approval("a-3", opened_at=None, source_subject=""))

    code = cli.main(["approvals"])

    lines = capsys.readouterr().out.splitlines()
    assert code == 0
    assert lines == [
        "a-1  2h 5m  Sync on Tuesday?  create_event, send_reply:confirmation",
        "a-3  unknown  (subject not recorded)  create_event, send_reply:confirmation",
    ]


def test_approvals_with_none_awaiting_says_so(
    approvals_environment: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    SqliteState(approvals_environment / "state.db")

    assert cli.main(["approvals"]) == 0
    assert capsys.readouterr().out == "No approval is waiting.\n"


def test_approvals_with_no_state_file_says_so_and_creates_none(
    approvals_environment: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = cli.main(["approvals"])

    out = capsys.readouterr().out
    assert code == 0
    assert out.count("\n") == 1 and "state.db" in out
    assert not (approvals_environment / "state.db").exists()


def test_approvals_never_migrates_an_older_state_file(
    approvals_environment: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = approvals_environment / "state.db"
    SqliteState(path).approval_save(awaiting_approval("a-1"))
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA user_version = 2")
    connection.commit()
    connection.close()
    before = path.read_bytes()

    code = cli.main(["approvals"])

    assert code == 0
    assert capsys.readouterr().out == (
        "The state file is from an older AECA: start AECA once to migrate the state file.\n"
    )
    assert path.read_bytes() == before


# --- aeca health: the heartbeat file and POLL_SECONDS alone ---


def _heartbeat_written(tmp_path: Path, *, seconds_ago: int) -> None:
    written_at = FIXED_NOW - timedelta(seconds=seconds_ago)
    (tmp_path / "heartbeat").write_text(written_at.isoformat() + "\n")


@pytest.fixture
def health_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    for name in ("GMAIL_ADDRESS", "GOOGLE_CLIENT_ID", "ANTHROPIC_API_KEY", "POLL_SECONDS"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("AECA_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(cli, "clock", FixedClock())
    return tmp_path


def test_health_passes_when_the_heartbeat_is_younger_than_three_polls(
    health_environment: Path,
) -> None:
    _heartbeat_written(health_environment, seconds_ago=89)

    assert cli.main(["health"]) == 0


def test_health_fails_when_the_heartbeat_is_older_than_three_polls(
    health_environment: Path,
) -> None:
    _heartbeat_written(health_environment, seconds_ago=90)

    assert cli.main(["health"]) == 1


def test_health_follows_poll_seconds_from_the_environment(
    health_environment: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("POLL_SECONDS", "60")
    _heartbeat_written(health_environment, seconds_ago=120)

    assert cli.main(["health"]) == 0


def test_health_fails_when_there_is_no_heartbeat_file(
    health_environment: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["health"]) == 1
    assert "heartbeat" in capsys.readouterr().out


def test_health_fails_when_the_heartbeat_is_far_in_the_future(health_environment: Path) -> None:
    _heartbeat_written(health_environment, seconds_ago=-91)

    assert cli.main(["health"]) == 1


def test_a_lost_lock_exits_one_with_its_next_action(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    run_environment: None,
    restored_signal_handlers: None,
) -> None:
    class LosesTheLock:
        def run(self, _stop: threading.Event) -> None:
            raise LockLost.for_a_lost_lock()

    monkeypatch.setattr(cli, "build_service", lambda _settings: LosesTheLock())

    code = cli.main(["run"])

    err = capsys.readouterr().err
    assert code == 1
    assert "no longer holds the inbox lock" in err and "FORCE_LOCK=true" in err
