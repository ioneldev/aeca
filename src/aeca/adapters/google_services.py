"""Builds the Gmail and Calendar clients, with the corporate CA when one is configured."""

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import google_auth_httplib2
import httplib2
from googleapiclient.discovery import build

from aeca.adapters.gmail import GmailMailbox
from aeca.adapters.google_auth import credentials_from_settings
from aeca.adapters.google_calendar import GoogleCalendar
from aeca.settings import Settings


@dataclass(frozen=True)
class GoogleServices:
    mailbox: GmailMailbox
    calendar: GoogleCalendar


def build_google_services(settings: Settings) -> GoogleServices:
    credentials = credentials_from_settings(settings)
    return GoogleServices(
        mailbox=GmailMailbox(
            _service("gmail", "v1", settings, credentials), settings.gmail_address
        ),
        calendar=GoogleCalendar(_service("calendar", "v3", settings, credentials)),
    )


def _service(name: str, version: str, settings: Settings, credentials: Any) -> Any:
    http = google_auth_httplib2.AuthorizedHttp(credentials, http=_http_for(settings.extra_ca_certs))
    return build(name, version, http=http, cache_discovery=False)


def _http_for(extra_ca_certs: Path | None) -> httplib2.Http:
    """httplib2 ignores SSL_CERT_FILE, so the CA file is handed to it directly.

    The proxy needs no code: Http() reads HTTPS_PROXY from the environment by default. The token
    refresh goes through this same Http, because AuthorizedHttp refreshes with a request built on
    it; the two variables below only cover any other library that honours them.
    """
    if extra_ca_certs is None:
        return httplib2.Http()
    os.environ["SSL_CERT_FILE"] = str(extra_ca_certs)
    os.environ["REQUESTS_CA_BUNDLE"] = str(extra_ca_certs)
    return httplib2.Http(ca_certs=str(extra_ca_certs))
