"""The image: what it holds, who it runs as, and whether it can say it is well.

The image is built from the checkout and run with `docker run` against a fake key in an env file
the test writes itself, so no test can load the real .env. The image is built once per checkout
under a test tag, so a test run never replaces the image a person built. Every test skips, saying
why, when Docker is not available.
"""

import gzip
import hashlib
import io
import json
import secrets
import shutil
import subprocess
import tarfile
from pathlib import Path
from typing import Any, cast

import pytest

pytestmark = pytest.mark.live

REPO_ROOT = Path(__file__).resolve().parents[1]
BUILD_TIMEOUT_S = 1800
RUN_TIMEOUT_S = 120
NON_ROOT_USER = "aeca"
NON_ROOT_UID = "1000"
SECRET_VARIABLES = ("ANTHROPIC_API_KEY", "GOOGLE_CLIENT_SECRET", "GOOGLE_REFRESH_TOKEN")


def _why_docker_is_missing() -> str | None:
    if shutil.which("docker") is None:
        return "docker is not on PATH"
    try:
        answered = subprocess.run(["docker", "info"], capture_output=True, text=True, timeout=60)
    except subprocess.TimeoutExpired:
        return "`docker info` did not answer within 60 s"
    if answered.returncode != 0:
        return f"`docker info` failed: {answered.stderr.strip()[:200]}"
    return None


@pytest.fixture(scope="session")
def docker() -> None:
    reason = _why_docker_is_missing()
    if reason is not None:
        pytest.skip(f"Docker is not available: {reason}")


# One tag per checkout, so two checkouts testing at once never run each other's image.
def _test_tag(name: str) -> str:
    checkout = hashlib.sha256(str(REPO_ROOT).encode()).hexdigest()[:12]
    return f"aeca-test-{name}:{checkout}"


def _build(context: Path, *, tag: str) -> None:
    built = subprocess.run(
        ["docker", "build", "--tag", tag, str(context)],
        capture_output=True,
        text=True,
        timeout=BUILD_TIMEOUT_S,
    )
    assert built.returncode == 0, built.stderr[-4000:]


@pytest.fixture(scope="session")
def image(docker: None) -> str:
    tag = _test_tag("runtime")
    _build(REPO_ROOT, tag=tag)
    return tag


def _a_fake_key() -> str:
    return "sk-ant-test-" + secrets.token_hex(24)


def _image_config(tag: str) -> dict[str, Any]:
    inspected = subprocess.run(
        ["docker", "image", "inspect", tag], capture_output=True, check=True, text=True
    )
    config: dict[str, Any] = json.loads(inspected.stdout)[0]["Config"]
    return config


def _run_in_image(tag: str, *shell_script: str, env_file: Path | None = None) -> str:
    options = ["--env-file", str(env_file)] if env_file is not None else []
    done = subprocess.run(
        ["docker", "run", "--rm", *options, "--entrypoint", "sh", tag, "-c", *shell_script],
        capture_output=True,
        text=True,
        timeout=RUN_TIMEOUT_S,
    )
    assert done.returncode == 0, done.stderr
    return done.stdout


# The key


def test_api_key_comes_from_env_file_only(image: str, tmp_path: Path) -> None:
    key = _a_fake_key()
    env_file = tmp_path / "run.env"
    env_file.write_text(f"ANTHROPIC_API_KEY={key}\n", encoding="utf-8")

    without_env_file = _run_in_image(image, 'printf %s "${ANTHROPIC_API_KEY-unset}"')
    with_env_file = _run_in_image(image, 'printf %s "$ANTHROPIC_API_KEY"', env_file=env_file)

    assert without_env_file == "unset"
    assert with_env_file == key


def test_the_image_environment_names_no_secret(image: str) -> None:
    baked_in = {entry.split("=", 1)[0] for entry in _image_config(image).get("Env") or []}

    assert baked_in.isdisjoint(SECRET_VARIABLES)


def test_no_layer_or_history_holds_a_key_from_the_build_folder(
    docker: None, tmp_path: Path
) -> None:
    planted = _a_fake_key()
    context = _a_copy_of_the_checkout(tmp_path / "context")
    for name in (".env", ".env.local"):
        (context / name).write_text(f"ANTHROPIC_API_KEY={planted}\n", encoding="utf-8")
    tag = _test_tag("planted")

    _build(context, tag=tag)

    assert not _image_holds(tag, planted.encode()), "the image holds the key"


# Only the files git would list, so the real .env, which git ignores, is never read or copied.
def _a_copy_of_the_checkout(destination: Path) -> Path:
    listed = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        cwd=REPO_ROOT,
        capture_output=True,
        check=True,
    ).stdout.decode()
    destination.mkdir()
    for name in filter(None, listed.split("\0")):
        source = REPO_ROOT / name
        if source.is_file():
            (destination / name).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination / name)
    return destination


def _image_holds(tag: str, secret: bytes) -> bool:
    history = subprocess.run(
        ["docker", "history", "--no-trunc", tag], capture_output=True, check=True
    ).stdout
    if secret in history or secret in _inspected_bytes(tag):
        return True
    return _any_saved_file_holds(tag, secret)


def _inspected_bytes(tag: str) -> bytes:
    return subprocess.run(
        ["docker", "image", "inspect", tag], capture_output=True, check=True
    ).stdout


def _any_saved_file_holds(tag: str, secret: bytes) -> bool:
    saved = subprocess.Popen(["docker", "save", tag], stdout=subprocess.PIPE)
    assert saved.stdout is not None
    try:
        with tarfile.open(fileobj=saved.stdout, mode="r|") as archive:
            return any(_member_holds(archive, member, secret) for member in archive)
    finally:
        saved.stdout.close()
        saved.wait()


# A layer may be compressed, and the key could straddle two chunks.
def _member_holds(archive: tarfile.TarFile, member: tarfile.TarInfo, secret: bytes) -> bool:
    extracted = archive.extractfile(member)
    if extracted is None:
        return False
    stream = cast(io.BufferedReader, extracted)
    source = gzip.GzipFile(fileobj=stream) if stream.peek(2)[:2] == b"\x1f\x8b" else stream
    tail = b""
    while chunk := source.read(1 << 20):
        if secret in tail + chunk:
            return True
        tail = chunk[-len(secret) :]
    return False


# Who it runs as


def test_runs_as_non_root(image: str) -> None:
    uid = _run_in_image(image, "id -u").strip()

    assert _image_config(image)["User"] == NON_ROOT_USER
    assert uid == NON_ROOT_UID


# Whether it can say it is well


@pytest.mark.xfail(strict=True, reason="Task 15 adds HEALTHCHECK")
def test_healthcheck_is_defined(image: str) -> None:
    healthcheck = _image_config(image).get("Healthcheck")

    assert healthcheck, "the image defines no HEALTHCHECK"
    assert healthcheck["Test"][0] != "NONE"
