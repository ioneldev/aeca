import socket
import ssl

import httplib2
import pytest
from fakes.google_stub import http_error
from google.auth.exceptions import RefreshError, TransportError

from aeca.adapters.google_errors import classify
from aeca.domain.errors import AecaError, CredentialsRevoked, EmailError, SystemWideError


@pytest.mark.parametrize("status", [429, 500, 502, 503, 504])
def test_429_5xx_are_system_wide(status: int) -> None:
    assert type(classify(http_error(status))) is SystemWideError


@pytest.mark.parametrize(
    "reason", ["rateLimitExceeded", "userRateLimitExceeded", "quotaExceeded", "backendError"]
)
def test_a_403_rate_limit_is_system_wide(reason: str) -> None:
    assert type(classify(http_error(403, reason))) is SystemWideError


def test_a_403_for_another_reason_is_specific_to_the_email() -> None:
    assert type(classify(http_error(403, "forbidden"))) is EmailError


@pytest.mark.parametrize(
    "error",
    [
        ConnectionResetError("reset"),
        TimeoutError("timed out"),
        socket.gaierror("no such host"),
        httplib2.ServerNotFoundError("Unable to find the server"),
        TransportError("network"),
    ],
)
def test_connection_errors_are_system_wide(error: Exception) -> None:
    assert type(classify(error)) is SystemWideError


@pytest.mark.parametrize(
    "error",
    [
        ssl.SSLCertVerificationError("certificate verify failed: self-signed certificate"),
        ssl.SSLError("handshake failure"),
    ],
)
def test_a_tls_failure_names_extra_ca_certs_and_the_proxy(error: Exception) -> None:
    classified = classify(error)

    assert type(classified) is SystemWideError
    assert "EXTRA_CA_CERTS" in str(classified) + classified.next_action
    assert "proxy" in str(classified)


def test_a_tls_failure_wrapped_by_the_token_refresh_is_still_tls() -> None:
    classified = classify(TransportError(ssl.SSLCertVerificationError("verify failed")))

    assert "EXTRA_CA_CERTS" in str(classified) + classified.next_action


def test_invalid_grant_names_make_auth() -> None:
    error = RefreshError("invalid_grant: Token has been expired or revoked.")

    classified = classify(error)

    assert isinstance(classified, CredentialsRevoked)
    assert "make auth" in classified.next_action


def test_a_401_means_the_credentials_are_revoked() -> None:
    assert isinstance(classify(http_error(401)), CredentialsRevoked)


def test_a_revoked_token_stops_the_whole_tick() -> None:
    assert isinstance(classify(RefreshError("invalid_grant")), SystemWideError)


def test_a_refresh_failure_that_is_not_invalid_grant_is_system_wide() -> None:
    classified = classify(RefreshError("server_error"))

    assert type(classified) is SystemWideError


@pytest.mark.parametrize("status", [400, 404, 409])
def test_anything_else_is_specific_to_the_email(status: int) -> None:
    assert type(classify(http_error(status))) is EmailError


def test_an_unknown_exception_is_specific_to_the_email() -> None:
    assert type(classify(ValueError("odd"))) is EmailError


def test_an_error_already_classified_passes_through() -> None:
    already = AecaError("x", next_action="y")

    assert classify(already) is already
