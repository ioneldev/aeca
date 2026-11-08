from aeca.domain.secret import SecretStr


def test_a_secret_shows_nothing_when_printed() -> None:
    secret = SecretStr("hunter2")
    assert (repr(secret), str(secret), f"{secret}") == ("***", "***", "***")


def test_a_secret_reveals_its_value_on_request() -> None:
    assert SecretStr("hunter2").reveal() == "hunter2"
