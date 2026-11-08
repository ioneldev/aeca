import errno
import http.client
import socket
import ssl

import httplib2
import pytest
from fakes.google_stub import http_error
from google.auth.exceptions import RefreshError, TransportError

from aeca.adapters.google_errors import classify, execute
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


class _StalledRequest:
    """What httplib2 raises once the socket's timeout passes (socket.timeout is TimeoutError)."""

    def execute(self) -> None:
        raise TimeoutError("timed out")


def test_a_request_that_times_out_ends_the_tick_as_system_wide() -> None:
    with pytest.raises(SystemWideError, match="could not be reached"):
        execute(_StalledRequest())


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


@pytest.mark.parametrize(
    "error",
    [
        OSError(errno.ENETUNREACH, "Network is unreachable"),
        OSError(errno.EHOSTUNREACH, "No route to host"),
        http.client.IncompleteRead(b"par"),
        http.client.RemoteDisconnected("closed"),
    ],
)
def test_raw_network_failures_are_system_wide(error: Exception) -> None:
    assert type(classify(error)) is SystemWideError


def test_a_tls_failure_still_names_the_proxy_though_it_is_an_oserror() -> None:
    assert "EXTRA_CA_CERTS" in classify(ssl.SSLCertVerificationError("x")).next_action


def test_a_missing_scope_means_make_auth_with_every_permission() -> None:
    classified = classify(http_error(403, "insufficientPermissions"))

    assert isinstance(classified, CredentialsRevoked)
    assert "make auth" in classified.next_action
    assert "every requested permission" in classified.next_action


def test_the_daily_limit_is_system_wide() -> None:
    assert type(classify(http_error(403, "dailyLimitExceeded"))) is SystemWideError


def test_a_disabled_api_names_the_cloud_project() -> None:
    classified = classify(http_error(403, "accessNotConfigured"))

    assert type(classified) is SystemWideError
    assert "Cloud project" in classified.next_action


def test_a_domain_policy_block_names_the_admin() -> None:
    classified = classify(http_error(403, "domainPolicy"))

    assert type(classified) is SystemWideError
    assert "domain admin" in classified.next_action


@pytest.mark.parametrize("reason", ["invalid_client", "unauthorized_client"])
def test_a_rejected_client_names_the_client_settings(reason: str) -> None:
    classified = classify(RefreshError(f"{reason}: Unauthorized"))

    assert isinstance(classified, CredentialsRevoked)
    assert "GOOGLE_CLIENT_ID" in classified.next_action
    assert "GOOGLE_CLIENT_SECRET" in classified.next_action
    assert "make auth" in classified.next_action
