from dataclasses import dataclass

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

from aeca.domain.errors import ConfigError
from aeca.domain.secret import SecretStr
from aeca.settings import Settings

GOOGLE_SCOPES: tuple[str, ...] = (
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/calendar.settings.readonly",
)
GOOGLE_AUTH_URI = "https://accounts.google.com/o/oauth2/auth"
GOOGLE_TOKEN_URI = "https://oauth2.googleapis.com/token"


@dataclass(frozen=True)
class ConsentRequest:
    client_id: str
    client_secret: SecretStr
    bind_address: str
    port: int


def run_consent(request: ConsentRequest) -> SecretStr:
    """Walks the user through Google's consent screen and returns the refresh token.

    The library prints the consent URL, which carries the client id and not the secret.
    """
    flow = InstalledAppFlow.from_client_config(_client_config(request), scopes=list(GOOGLE_SCOPES))
    credentials = flow.run_local_server(
        host="localhost",
        bind_addr=request.bind_address,
        port=request.port,
        open_browser=False,
        access_type="offline",
        prompt="consent",
    )
    if not credentials.refresh_token:
        raise ConfigError(
            "Google returned no refresh token.",
            next_action=(
                "Revoke AECA's access at myaccount.google.com/permissions, "
                "then run make auth again."
            ),
        )
    return SecretStr(credentials.refresh_token)


def credentials_from_settings(settings: Settings) -> Credentials:
    if settings.google_refresh_token is None:
        raise ConfigError(
            "GOOGLE_REFRESH_TOKEN is not set.",
            next_action="Run make auth, then run again.",
        )
    return Credentials(  # type: ignore[no-untyped-call]
        token=None,
        refresh_token=settings.google_refresh_token.reveal(),
        client_id=settings.google_client_id,
        client_secret=settings.google_client_secret.reveal(),
        token_uri=GOOGLE_TOKEN_URI,
        scopes=list(GOOGLE_SCOPES),
    )


def _client_config(request: ConsentRequest) -> dict[str, dict[str, str]]:
    return {
        "installed": {
            "client_id": request.client_id,
            "client_secret": request.client_secret.reveal(),
            "auth_uri": GOOGLE_AUTH_URI,
            "token_uri": GOOGLE_TOKEN_URI,
        }
    }
