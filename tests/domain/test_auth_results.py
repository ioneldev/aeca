import pytest

from aeca.domain.auth_results import AuthResults, parse_authentication_results


def test_topmost_google_result_wins_over_a_forged_lower_one():
    values = [
        "mx.google.com; dmarc=fail header.from=x.com",
        "mx.google.com; dmarc=pass header.from=x.com",
    ]
    assert parse_authentication_results(values).dmarc == "fail"


def test_results_from_another_server_are_ignored():
    assert parse_authentication_results(["evil.example; dmarc=pass"]).dmarc is None


def test_a_forged_header_above_nothing_of_googles_gives_no_results():
    assert parse_authentication_results(["evil.example; spf=pass; dkim=pass; dmarc=pass"]) == (
        AuthResults(spf=None, dkim=None, dmarc=None)
    )


def test_a_forged_header_above_googles_is_skipped_for_googles():
    values = ["evil.example; dmarc=pass", "mx.google.com; dmarc=fail"]
    assert parse_authentication_results(values).dmarc == "fail"


def test_no_header_gives_no_results():
    assert parse_authentication_results([]) == AuthResults(spf=None, dkim=None, dmarc=None)


def test_all_three_methods_are_read_and_lowercased():
    value = (
        "mx.google.com;\r\n"
        "       dkim=PASS header.i=@x.com header.s=s1;\r\n"
        "       spf=Pass (google.com: domain of a@x.com designates 1.2.3.4 as sender);\r\n"
        "       dmarc=Fail (p=REJECT sp=REJECT dis=NONE) header.from=x.com"
    )
    assert parse_authentication_results([value]) == AuthResults(
        spf="pass", dkim="pass", dmarc="fail"
    )


def test_the_authserv_id_is_compared_case_insensitively_and_ignores_a_version():
    assert parse_authentication_results(["MX.Google.com 1; dmarc=pass"]).dmarc == "pass"


def test_a_server_name_that_merely_contains_googles_is_not_googles():
    assert parse_authentication_results(["mx.google.com.evil.example; dmarc=pass"]).dmarc is None


def test_a_result_named_inside_a_comment_is_not_read():
    value = "mx.google.com; spf=fail (not dmarc=pass; really)"
    assert parse_authentication_results([value]).dmarc is None


@pytest.mark.parametrize("method", ["spf", "dkim", "dmarc"])
def test_a_missing_method_is_none(method):
    results = parse_authentication_results(["mx.google.com; none"])
    assert getattr(results, method) is None
