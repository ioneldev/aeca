"""Builds the Gmail and Calendar clients, with the corporate CA when one is configured."""

import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import google_auth_httplib2
import httplib2
from googleapiclient.discovery import build

from aeca.adapters.gmail import GmailMailbox
from aeca.adapters.google_auth import credentials_from_settings
from aeca.adapters.google_calendar import GoogleCalendar
from aeca.domain.errors import ConfigError
from aeca.settings import Settings

COMBINED_BUNDLE_NAME = "ca-bundle.pem"


@dataclass(frozen=True)
class GoogleServices:
    mailbox: GmailMailbox
    calendar: GoogleCalendar


def build_google_services(settings: Settings) -> GoogleServices:
    credentials = credentials_from_settings(settings)
    http_factory = _http_factory(settings)
    return GoogleServices(
        mailbox=GmailMailbox(
            _service(ServiceSpec("gmail", "v1"), credentials, http_factory), settings.gmail_address
        ),
        calendar=GoogleCalendar(_service(ServiceSpec("calendar", "v3"), credentials, http_factory)),
    )


@dataclass(frozen=True)
class ServiceSpec:
    name: str
    version: str


def _service(spec: ServiceSpec, credentials: Any, http_factory: Callable[[], httplib2.Http]) -> Any:
    http = google_auth_httplib2.AuthorizedHttp(credentials, http=http_factory())
    return build(spec.name, spec.version, http=http, cache_discovery=False)


def _http_factory(settings: Settings) -> Callable[[], httplib2.Http]:
    """httplib2 ignores SSL_CERT_FILE, so the CA bundle is handed to it directly.

    The proxy needs no code: Http() reads HTTPS_PROXY from the environment by default. The token
    refresh goes through this same Http, because AuthorizedHttp refreshes with a request built on
    it; the two variables set below only cover any other library that honours them.
    """
    if settings.extra_ca_certs is None:
        return _default_http
    bundle = str(_write_combined_bundle(settings.extra_ca_certs, settings.data_dir))
    os.environ["SSL_CERT_FILE"] = bundle
    os.environ["REQUESTS_CA_BUNDLE"] = bundle

    def http_trusting_bundle() -> httplib2.Http:
        return httplib2.Http(ca_certs=bundle)

    return http_trusting_bundle


def _default_http() -> httplib2.Http:
    return httplib2.Http()


def _write_combined_bundle(extra_ca_certs: Path, data_dir: Path) -> Path:
    """The extra CA is trusted in addition to the default roots, not instead of them: the proxy
    may intercept some hosts and not others."""
    try:
        extra = extra_ca_certs.read_bytes()
    except OSError as error:
        raise ConfigError(
            f"EXTRA_CA_CERTS={extra_ca_certs} cannot be read: {error.strerror}.",
            next_action="Point EXTRA_CA_CERTS at the proxy's root certificate file, then run again",
        ) from error
    bundle = data_dir / COMBINED_BUNDLE_NAME
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
        bundle.write_bytes(Path(httplib2.CA_CERTS).read_bytes() + b"\n" + extra)
    except OSError as error:
        raise ConfigError(
            f"AECA_DATA_DIR={data_dir} cannot be written: {error.strerror}.",
            next_action="Point AECA_DATA_DIR at a writable folder, then run again",
        ) from error
    return bundle
