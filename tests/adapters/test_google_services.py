import os
from pathlib import Path
from typing import Any

import httplib2
import pytest

from aeca.adapters import google_services
from aeca.adapters.google_services import build_google_services
from aeca.domain.errors import ConfigError
from aeca.settings import load_settings


class RecordingBuild:
    def __init__(self) -> None:
        self.https: list[Any] = []

    def __call__(self, name: str, version: str, *, http: Any, cache_discovery: bool) -> Any:
        assert cache_discovery is False
        self.https.append(http)
        return _ServiceShell()


class _ServiceShell:
    def users(self) -> "_ServiceShell":
        return self

    def messages(self) -> "_ServiceShell":
        return self

    threads = drafts = labels = events = settings = messages


@pytest.fixture
def built(monkeypatch: pytest.MonkeyPatch) -> RecordingBuild:
    recorder = RecordingBuild()
    monkeypatch.setattr(google_services, "build", recorder)
    monkeypatch.setattr(google_services, "credentials_from_settings", lambda _settings: None)
    # setenv first, so monkeypatch has something to restore when the code under test overwrites it.
    monkeypatch.setenv("SSL_CERT_FILE", "untouched")
    monkeypatch.setenv("REQUESTS_CA_BUNDLE", "untouched")
    monkeypatch.delenv("SSL_CERT_FILE")
    monkeypatch.delenv("REQUESTS_CA_BUNDLE")
    return recorder


def settings_with_ca(env: dict[str, str], ca_file: Path, data_dir: Path) -> Any:
    return load_settings(
        env | {"EXTRA_CA_CERTS": str(ca_file), "AECA_DATA_DIR": str(data_dir)}, purpose="run"
    )


def test_extra_ca_certs_are_trusted_in_addition_to_the_default_roots(
    built: RecordingBuild, minimal_run_env: dict[str, str], tmp_path: Path
) -> None:
    ca_file = tmp_path / "proxy-ca.pem"
    ca_file.write_text("-----PROXY CA-----\n")
    data_dir = tmp_path / "data"

    build_google_services(settings_with_ca(minimal_run_env, ca_file, data_dir))

    gmail_http, calendar_http = (authorized.http for authorized in built.https)
    bundle = Path(gmail_http.ca_certs)
    assert isinstance(gmail_http, httplib2.Http)
    assert calendar_http.ca_certs == gmail_http.ca_certs
    assert bundle.parent == data_dir
    assert bundle.read_text().endswith("-----PROXY CA-----\n")
    assert Path(httplib2.CA_CERTS).read_text() in bundle.read_text()
    assert os.environ["SSL_CERT_FILE"] == os.environ["REQUESTS_CA_BUNDLE"] == str(bundle)


def test_a_missing_ca_file_is_a_config_error_naming_the_variable_and_path(
    built: RecordingBuild, minimal_run_env: dict[str, str], tmp_path: Path
) -> None:
    missing = tmp_path / "nope.pem"

    with pytest.raises(ConfigError) as raised:
        build_google_services(settings_with_ca(minimal_run_env, missing, tmp_path))

    assert "EXTRA_CA_CERTS" in str(raised.value)
    assert str(missing) in str(raised.value)


def test_an_unwritable_data_dir_is_a_config_error_naming_the_variable_and_path(
    built: RecordingBuild, minimal_run_env: dict[str, str], tmp_path: Path
) -> None:
    ca_file = tmp_path / "proxy-ca.pem"
    ca_file.write_text("-----PROXY CA-----\n")
    not_a_directory = tmp_path / "file"
    not_a_directory.write_text("")

    with pytest.raises(ConfigError) as raised:
        build_google_services(settings_with_ca(minimal_run_env, ca_file, not_a_directory))

    assert "AECA_DATA_DIR" in str(raised.value)
    assert str(not_a_directory) in str(raised.value)


def test_without_extra_ca_certs_the_defaults_stand(
    built: RecordingBuild, minimal_run_env: dict[str, str]
) -> None:
    build_google_services(load_settings(minimal_run_env, purpose="run"))

    assert built.https[0].http.ca_certs is None
    assert "SSL_CERT_FILE" not in os.environ


def test_the_ca_tests_leave_the_environment_as_they_found_it() -> None:
    # Runs after the tests above; a leak from any of them shows here.
    assert "SSL_CERT_FILE" not in os.environ
    assert "REQUESTS_CA_BUNDLE" not in os.environ
