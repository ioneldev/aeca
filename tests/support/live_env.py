"""Rebuilds the run environment for live tests from the secrets the guard took out."""

from collections.abc import Mapping

from aeca.domain.secret import SecretStr

REQUIRED_SECRETS = ("GOOGLE_CLIENT_SECRET", "GOOGLE_REFRESH_TOKEN")


def rebuild_run_env(
    held_secrets: Mapping[str, SecretStr], environ: Mapping[str, str]
) -> dict[str, str] | None:
    """The environment with every held secret put back, or None when a required one is missing.

    The result holds plain secrets, so it goes only into load_settings and is never printed.
    """
    if any(name not in held_secrets for name in REQUIRED_SECRETS):
        return None
    return {**environ, **{name: secret.reveal() for name, secret in held_secrets.items()}}
