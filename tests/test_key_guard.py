"""The guard in conftest.py, proved on real pytest runs that fail on purpose with fake secrets."""

from pathlib import Path

import pytest

CONFTEST = Path(__file__).with_name("conftest.py")
# Built, so a failure of these tests prints the expression and not the whole value.
FAKE_SECRETS = {
    "ANTHROPIC_API_KEY": "sk-ant-api03-" + "AbCdEfGh" * 11 + "-Zz",
    "GOOGLE_CLIENT_SECRET": "GOCSPX-" + "QrStUvWx" * 4 + "-Yy",
    "GOOGLE_REFRESH_TOKEN": "1//0g" + "MnOpQrSt" * 8 + "-Xx",
}
FRAGMENT_LENGTH = 16
REPORT_MODES = {
    "default": (),
    "showlocals": ("-l",),
    "long traceback": ("--tb=long",),
    "full trace": ("--full-trace",),
    "very verbose": ("-vv",),
    "report passes": ("-rA",),
}

LEAKING_TESTS = """
import os
from dataclasses import dataclass

SECRET = os.environ["LEAKED_SECRET"]


@dataclass
class HoldsTheSecret:
    secret: str


@dataclass
class CutByRepr:
    before: str
    secret: str
    after: str = "t" * 400


def check(argument):
    assert argument is None


def test_a_repr_prints_the_secret():
    check(HoldsTheSecret(secret=SECRET))


def test_a_helper_argument_prints_the_secret():
    check(SECRET)


def test_a_compared_dict_prints_the_secret():
    assert {"authorization": f"Bearer {SECRET}"} == {}


def test_a_print_puts_the_secret_in_captured_output():
    print("debug:", SECRET)
    assert False


def test_a_shortened_repr_prints_part_of_the_secret():
    check(CutByRepr(before="b" * 70, secret=SECRET))


def test_a_clean_failure_keeps_its_message():
    assert 1 == 2, "CLEAN-FAILURE-MESSAGE"
"""


def fragments_of(secret: str) -> list[str]:
    return [
        secret[start : start + FRAGMENT_LENGTH]
        for start in range(len(secret) - FRAGMENT_LENGTH + 1)
    ]


@pytest.fixture
def guarded(pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch) -> pytest.Pytester:
    pytester.makeconftest(CONFTEST.read_text(encoding="utf-8"))
    for name, value in FAKE_SECRETS.items():
        monkeypatch.setenv(name, value)
    return pytester


def everything_printed(result: pytest.RunResult) -> str:
    return result.stdout.str() + result.stderr.str()


@pytest.mark.parametrize("mode", REPORT_MODES.values(), ids=REPORT_MODES.keys())
@pytest.mark.parametrize("variable", FAKE_SECRETS)
def test_no_fragment_of_a_secret_survives_a_failing_run(
    guarded: pytest.Pytester, monkeypatch: pytest.MonkeyPatch, variable: str, mode: tuple[str, ...]
) -> None:
    guarded.makepyfile(LEAKING_TESTS)
    # The pytester run reads the secret from here, because conftest pops the real variables.
    monkeypatch.setenv("LEAKED_SECRET", FAKE_SECRETS[variable])

    result = guarded.runpytest_inprocess(*mode)

    result.assert_outcomes(failed=6)
    printed = everything_printed(result)
    assert "[REDACTED]" in printed, "the guard proves nothing unless it had a secret to remove"
    assert "CLEAN-FAILURE-MESSAGE" in printed
    assert [f for f in fragments_of(FAKE_SECRETS[variable]) if f in printed] == []


def test_a_secret_passed_through_a_fixture_failure_is_scrubbed_too(
    guarded: pytest.Pytester,
) -> None:
    guarded.makepyfile(
        """
        import pytest

        @pytest.fixture
        def broken(api_key):
            raise ValueError(f"bad value {api_key.reveal()}")

        def test_never_runs(broken):
            pass
        """
    )

    result = guarded.runpytest_inprocess()

    result.assert_outcomes(errors=1)
    assert [
        f
        for f in fragments_of(FAKE_SECRETS["ANTHROPIC_API_KEY"])
        if f in everything_printed(result)
    ] == []


def test_the_secrets_leave_the_environment_before_any_test_runs(guarded: pytest.Pytester) -> None:
    guarded.makepyfile(
        f"""
        import os

        def test_the_environment_holds_no_secret():
            assert [name for name in {list(FAKE_SECRETS)!r} if name in os.environ] == []
        """
    )

    guarded.runpytest_inprocess().assert_outcomes(passed=1)


def test_tests_get_the_api_key_as_a_secret(guarded: pytest.Pytester) -> None:
    guarded.makepyfile(
        """
        from aeca.domain.secret import SecretStr

        def test_a_secret_str(api_key):
            assert isinstance(api_key, SecretStr)
        """
    )

    guarded.runpytest_inprocess().assert_outcomes(passed=1)


def test_a_test_can_ask_whether_text_holds_a_secret(guarded: pytest.Pytester) -> None:
    secret = FAKE_SECRETS["GOOGLE_REFRESH_TOKEN"]
    guarded.makepyfile(
        f"""
        def test_whole_and_part(holds_secret):
            secret = {secret!r}
            assert holds_secret(f"header: {{secret}}")
            assert holds_secret(f"cut: {{secret[20:60]}}...")
            assert not holds_secret("no secret in this text")
        """
    )

    guarded.runpytest_inprocess().assert_outcomes(passed=1)


def test_skips_and_expected_failures_still_report_with_a_secret_set(
    guarded: pytest.Pytester,
) -> None:
    guarded.makepyfile(
        """
        import pytest

        def test_skipped():
            pytest.skip("not today")

        @pytest.mark.xfail
        def test_expected_failure():
            assert False
        """
    )

    result = guarded.runpytest_inprocess("-rsx")

    result.assert_outcomes(skipped=1, xfailed=1)
    result.stdout.fnmatch_lines(["*not today*"])


def test_a_run_without_a_key_tells_the_user_how_to_get_one(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    pytester.makeconftest(CONFTEST.read_text(encoding="utf-8"))
    for name in FAKE_SECRETS:
        monkeypatch.delenv(name, raising=False)
    pytester.makepyfile("def test_needs_the_key(api_key):\n    pass\n")

    result = pytester.runpytest_inprocess()

    result.assert_outcomes(errors=1)
    result.stdout.fnmatch_lines(["*make eval*"])


def test_a_run_without_secrets_leaves_failure_reports_as_pytest_wrote_them(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    pytester.makeconftest(CONFTEST.read_text(encoding="utf-8"))
    for name in FAKE_SECRETS:
        monkeypatch.delenv(name, raising=False)
    pytester.makepyfile(test_a="def test_fails():\n    assert 1 == 2, 'plain failure'\n")

    result = pytester.runpytest_inprocess()

    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(["FAILED *test_fails - AssertionError: plain failure*"])
