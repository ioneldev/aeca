from support.live_env import rebuild_run_env

from aeca.domain.secret import SecretStr

HELD = {
    "GOOGLE_CLIENT_SECRET": SecretStr("client-secret-value"),
    "GOOGLE_REFRESH_TOKEN": SecretStr("refresh-token-value"),
}


def test_the_held_secrets_join_the_plain_variables() -> None:
    env = rebuild_run_env(HELD, {"GMAIL_ADDRESS": "a@gmail.com"})

    assert env == {
        "GMAIL_ADDRESS": "a@gmail.com",
        "GOOGLE_CLIENT_SECRET": "client-secret-value",
        "GOOGLE_REFRESH_TOKEN": "refresh-token-value",
    }


def test_an_absent_required_secret_gives_nothing_to_run_with() -> None:
    assert rebuild_run_env({"GOOGLE_CLIENT_SECRET": HELD["GOOGLE_CLIENT_SECRET"]}, {}) is None
    assert rebuild_run_env({}, {"GMAIL_ADDRESS": "a@gmail.com"}) is None
