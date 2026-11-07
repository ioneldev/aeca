"""What a Docker make target checks before it starts anything: each missing tool or setting is
named with its fix.

The script runs with a stand-in docker on PATH, so no test needs Docker, and with an env file of
the test's own, so no test reads the real .env.
"""

import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "preflight.sh"
SECRET = "fake-secret-0123456789-not-real"
AUTH_ENV = f"GMAIL_ADDRESS=a@b.c\nGOOGLE_CLIENT_ID=id\nGOOGLE_CLIENT_SECRET={SECRET}\n"
RUN_ENV = AUTH_ENV + f"GOOGLE_REFRESH_TOKEN={SECRET}\nANTHROPIC_API_KEY={SECRET}\n"


def a_docker(bin_dir: Path, *, compose: bool, daemon: bool) -> None:
    bin_dir.mkdir(exist_ok=True)
    docker = bin_dir / "docker"
    docker.write_text(
        "#!/bin/sh\n"
        'case "$1" in\n'
        f"  compose) exit {0 if compose else 1} ;;\n"
        f"  info) exit {0 if daemon else 1} ;;\n"
        "esac\n"
        "exit 0\n"
    )
    docker.chmod(0o755)


def preflight(
    tmp_path: Path,
    *checks: str,
    env_text: str | None = RUN_ENV,
    docker: bool = True,
    compose: bool = True,
    daemon: bool = True,
) -> subprocess.CompletedProcess[str]:
    bin_dir = tmp_path / "bin"
    if docker:
        a_docker(bin_dir, compose=compose, daemon=daemon)
    env_file = tmp_path / ".env"
    if env_text is not None:
        env_file.write_text(env_text)
    return subprocess.run(
        ["/bin/sh", str(SCRIPT), *checks],
        env={"PATH": f"{bin_dir}:/usr/bin:/bin", "AECA_ENV_FILE": str(env_file)},
        capture_output=True,
        text=True,
        check=False,
    )


def test_a_ready_machine_passes_quietly(tmp_path: Path) -> None:
    done = preflight(tmp_path, "docker", "env-run")

    assert (done.returncode, done.stdout, done.stderr) == (0, "", "")


@pytest.mark.parametrize(
    ("setup", "problem", "fix"),
    [
        ({"docker": False}, "docker is not installed", "Install Docker Desktop"),
        ({"compose": False}, "docker compose is not available", "Compose v2 plugin"),
        ({"daemon": False}, "Docker is not running", "Start Docker"),
        ({"env_text": None}, ".env is missing", "cp .env.example .env"),
    ],
)
def test_each_missing_part_of_docker_is_named_with_its_fix(
    tmp_path: Path, setup: dict[str, object], problem: str, fix: str
) -> None:
    done = preflight(tmp_path, "docker", **setup)  # type: ignore[arg-type]

    first, second = done.stderr.splitlines()
    assert done.returncode == 1
    assert first.startswith("preflight: ") and problem in first
    assert second.startswith("  next: ") and fix in second


@pytest.mark.parametrize(
    "env_text",
    [
        "",
        "GMAIL_ADDRESS=\n",
        "GMAIL_ADDRESS= \n",
        'GMAIL_ADDRESS=""\n',
        "GMAIL_ADDRESS=''\n",
        "GMAIL_ADDRESS= # the address goes here\n",
        f"# GMAIL_ADDRESS={SECRET}\n",
        f"OTHER_GMAIL_ADDRESS={SECRET}\n",
    ],
)
def test_a_variable_that_is_not_set_stops_the_run(tmp_path: Path, env_text: str) -> None:
    done = preflight(tmp_path, "env-auth", env_text=env_text)

    assert done.returncode == 1
    assert "GMAIL_ADDRESS" in done.stderr
    assert "NAME=<value>" in done.stderr


def test_every_missing_variable_is_named_at_once(tmp_path: Path) -> None:
    done = preflight(tmp_path, "env-run", env_text=AUTH_ENV)

    assert "GOOGLE_REFRESH_TOKEN ANTHROPIC_API_KEY" in done.stderr
    assert "GMAIL_ADDRESS" not in done.stderr


@pytest.mark.parametrize(
    "env_text",
    [
        AUTH_ENV,
        AUTH_ENV.replace("=", '="', 1).replace("\n", '"\n', 1),
        "export " + AUTH_ENV.replace("\n", "\nexport ").removesuffix("export "),
        AUTH_ENV.rstrip("\n"),
    ],
)
def test_variables_that_are_set_pass(tmp_path: Path, env_text: str) -> None:
    assert preflight(tmp_path, "env-auth", env_text=env_text).returncode == 0


def test_auth_needs_no_token_or_api_key(tmp_path: Path) -> None:
    assert preflight(tmp_path, "docker", "env-auth", env_text=AUTH_ENV).returncode == 0


def test_no_value_reaches_the_output(tmp_path: Path) -> None:
    passed = preflight(tmp_path, "docker", "env-run")
    failed_for_docker = preflight(tmp_path, "docker", "env-run", daemon=False)
    failed_for_a_variable = preflight(tmp_path, "env-run", env_text=AUTH_ENV)

    for done in (passed, failed_for_docker, failed_for_a_variable):
        assert SECRET not in done.stdout + done.stderr
        assert SECRET[:16] not in done.stdout + done.stderr


def test_a_check_it_does_not_know_is_a_mistake(tmp_path: Path) -> None:
    done = preflight(tmp_path, "tools")

    assert done.returncode == 2
    assert "preflight: no check named tools" in done.stderr


@pytest.mark.parametrize(
    ("target", "checks"),
    [
        ("image", ["docker"]),
        ("preflight", ["docker", "env-run"]),
        ("auth", ["docker", "env-auth"]),
    ],
)
def test_each_docker_target_runs_its_checks_first(target: str, checks: list[str]) -> None:
    planned = subprocess.run(
        ["make", "--no-print-directory", "-n", target],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )

    assert planned.stdout.splitlines()[0] == f"sh scripts/preflight.sh {' '.join(checks)}"
