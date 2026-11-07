import os
from pathlib import Path
from typing import Any

import httplib2
import pytest

from aeca.adapters import google_services
from aeca.adapters.google_services import build_google_services
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
    monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    monkeypatch.delenv("REQUESTS_CA_BUNDLE", raising=False)
    return recorder


def test_extra_ca_certs_reach_httplib2_and_the_environment(
    built: RecordingBuild, minimal_run_env: dict[str, str], tmp_path: Path
) -> None:
    ca_file = tmp_path / "proxy-ca.pem"
    settings = load_settings(minimal_run_env | {"EXTRA_CA_CERTS": str(ca_file)}, purpose="run")

    build_google_services(settings)

    gmail_http, calendar_http = (authorized.http for authorized in built.https)
    assert isinstance(gmail_http, httplib2.Http)
    assert gmail_http.ca_certs == calendar_http.ca_certs == str(ca_file)
    assert os.environ["SSL_CERT_FILE"] == os.environ["REQUESTS_CA_BUNDLE"] == str(ca_file)


def test_without_extra_ca_certs_the_defaults_stand(
    built: RecordingBuild, minimal_run_env: dict[str, str]
) -> None:
    build_google_services(load_settings(minimal_run_env, purpose="run"))

    assert built.https[0].http.ca_certs is None
    assert "SSL_CERT_FILE" not in os.environ
