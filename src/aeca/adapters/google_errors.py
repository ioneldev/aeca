"""Turns whatever the Google client raises into AECA's error kinds."""

import http.client
import json
import ssl
from typing import Any

import httplib2
from google.auth.exceptions import RefreshError, TransportError
from googleapiclient.errors import HttpError

from aeca.domain.errors import AecaError, CredentialsRevoked, EmailError, SystemWideError

RATE_LIMIT_REASONS = frozenset(
    {
        "rateLimitExceeded",
        "userRateLimitExceeded",
        "quotaExceeded",
        "backendError",
        "dailyLimitExceeded",
    }
)
MISSING_SCOPE_REASON = "insufficientPermissions"
# Each of these 403s hits every call, so no email is to blame. The value is the fix to name.
SETUP_BLOCKS = {
    "accessNotConfigured": "Enable the Gmail and Calendar APIs in the Cloud project",
    "domainPolicy": "Ask the domain admin to allow the Gmail and Calendar APIs, then run again",
}
CLIENT_REJECTED_MARKERS = ("invalid_client", "unauthorized_client")
# Any OSError is a network failure here: ENETUNREACH, EHOSTUNREACH, resets and timeouts all reach
# us raw, because httplib2 re-raises them. TLS errors are OSErrors too and are checked first.
CONNECTION_ERRORS = (OSError, http.client.HTTPException, httplib2.HttpLib2Error, TransportError)
UNAUTHORIZED = 401
TOO_MANY_REQUESTS = 429
FORBIDDEN = 403
FIRST_SERVER_ERROR = 500


def classify(error: Exception) -> AecaError:
    if isinstance(error, AecaError):
        return error
    if _is_tls_failure(error):
        return _tls_failure(error)
    if isinstance(error, RefreshError):
        return _refresh_failure(error)
    if isinstance(error, HttpError):
        return _http_failure(error)
    if isinstance(error, CONNECTION_ERRORS):
        return SystemWideError(
            f"Google could not be reached: {error}", next_action="Wait; AECA backs off and retries"
        )
    return EmailError(f"Google rejected the request: {error}", next_action="See the audit log")


def execute(request: Any) -> Any:
    try:
        return request.execute()
    except Exception as error:
        raise classify(error) from error


def execute_unless_missing(
    request: Any, *, missing_statuses: tuple[int, ...] = (404,)
) -> Any | None:
    """The reply, or None when Google says the thing is not there."""
    try:
        return request.execute()
    except HttpError as error:
        if error.resp.status in missing_statuses:
            return None
        raise classify(error) from error
    except Exception as error:
        raise classify(error) from error


def _is_tls_failure(error: BaseException) -> bool:
    # The token refresh wraps what httplib2 raised, so the TLS error sits one level down.
    causes = (error, error.__cause__, *error.args)
    return any(isinstance(cause, ssl.SSLError) for cause in causes)


def _tls_failure(error: Exception) -> SystemWideError:
    return SystemWideError(
        f"The TLS connection to Google failed ({error}). A corporate proxy that intercepts "
        "HTTPS is the likely cause.",
        next_action="Set EXTRA_CA_CERTS to the proxy's root certificate file, then run again",
    )


def _refresh_failure(error: RefreshError) -> SystemWideError:
    text = str(error)
    if "invalid_grant" in text:
        return CredentialsRevoked(
            "Google refused the stored token: it was revoked or has expired.",
            next_action="Run make auth",
        )
    if any(marker in text for marker in CLIENT_REJECTED_MARKERS):
        return CredentialsRevoked(
            f"Google rejected the OAuth client: {text}",
            next_action="Check GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET, then run make auth",
        )
    return SystemWideError(
        f"Google would not refresh the access token: {error}",
        next_action="Wait; AECA backs off and retries",
    )


def _http_failure(error: HttpError) -> AecaError:
    status = error.resp.status
    reasons = _reasons_of(error)
    if status == UNAUTHORIZED:
        return CredentialsRevoked(
            "Google answered 401: the credentials are no longer valid.",
            next_action="Run make auth",
        )
    if status == FORBIDDEN and MISSING_SCOPE_REASON in reasons:
        return CredentialsRevoked(
            "Google answered 403: a permission was not granted at consent.",
            next_action="Run make auth and allow every requested permission",
        )
    if status == FORBIDDEN and reasons & SETUP_BLOCKS.keys():
        fix = SETUP_BLOCKS[next(iter(reasons & SETUP_BLOCKS.keys()))]
        return SystemWideError("Google answered 403: an API is not available.", next_action=fix)
    if _is_overload(status, reasons):
        return SystemWideError(
            f"Google is limiting or failing requests (HTTP {status}).",
            next_action="Wait; AECA backs off and retries",
        )
    return EmailError(
        f"Google rejected the request (HTTP {status}): {error}", next_action="See the audit log"
    )


def _is_overload(status: int, reasons: set[str]) -> bool:
    if status == TOO_MANY_REQUESTS or status >= FIRST_SERVER_ERROR:
        return True
    return status == FORBIDDEN and bool(reasons & RATE_LIMIT_REASONS)


def _reasons_of(error: HttpError) -> set[str]:
    try:
        body = json.loads(error.content.decode("utf-8"))
        return {entry["reason"] for entry in body["error"]["errors"] if "reason" in entry}
    except (ValueError, KeyError, TypeError, AttributeError):
        return set()
