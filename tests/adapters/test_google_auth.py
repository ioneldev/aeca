from typing import Any

import pytest

from aeca.adapters import google_auth
from aeca.adapters.google_auth import ConsentRequest, credentials_from_settings, run_consent
from aeca.domain.errors import ConfigError
from aeca.domain.secret import SecretStr
from aeca.settings import load_settings


class FlowReturning:
    """Stands in for InstalledAppFlow, so no test opens a socket or reaches Google."""

    def __init__(self, refresh_token: str | None) -> None:
        self.credentials = type("Credentials", (), {"refresh_token": refresh_token})()
        self.served_with: dict[str, Any] = {}

    def from_client_config(self, *_args: object, **_kwargs: object) -> "FlowReturning":
        return self

    def run_local_server(self, **kwargs: Any) -> object:
        self.served_with = kwargs
        return self.credentials


def a_request() -> ConsentRequest:
    return ConsentRequest(
        client_id="id", client_secret=SecretStr("secret"), bind_address="0.0.0.0", port=8765
    )


def test_consent_returns_the_refresh_token_and_serves_as_the_brief_says(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    flow = FlowReturning("1//refresh")
    monkeypatch.setattr(google_auth, "InstalledAppFlow", flow)

    token = run_consent(a_request())

    assert token.reveal() == "1//refresh"
    assert flow.served_with == {
        "host": "localhost",
        "bind_addr": "0.0.0.0",
        "port": 8765,
        "open_browser": False,
        "access_type": "offline",
        "prompt": "consent",
    }


def test_consent_without_a_refresh_token_says_how_to_get_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(google_auth, "InstalledAppFlow", FlowReturning(None))

    with pytest.raises(ConfigError) as raised:
        run_consent(a_request())

    assert "myaccount.google.com/permissions" in raised.value.next_action
    assert "make auth" in raised.value.next_action


def test_credentials_are_refreshable_and_carry_the_scopes(minimal_run_env: dict[str, str]) -> None:
    credentials = credentials_from_settings(load_settings(minimal_run_env, purpose="run"))

    assert credentials.refresh_token == minimal_run_env["GOOGLE_REFRESH_TOKEN"]
    assert credentials.client_id == minimal_run_env["GOOGLE_CLIENT_ID"]
    assert tuple(credentials.scopes or ()) == google_auth.GOOGLE_SCOPES
