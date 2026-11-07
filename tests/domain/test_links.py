from urllib.parse import quote

import pytest

from aeca.domain.links import Link, extract_links, unwrap


def test_safe_links_and_urldefense_are_unwrapped():
    assert (
        unwrap(
            "https://eur01.safelinks.protection.outlook.com/?url=https%3A%2F%2Fexample.com%2Fx&data=1"
        )
        == "https://example.com/x"
    )
    assert (
        unwrap("https://urldefense.com/v3/__https://example.com/x__;!!abc")
        == "https://example.com/x"
    )


def test_an_ordinary_url_is_not_changed():
    assert unwrap("https://example.com/a?url=b") == "https://example.com/a?url=b"


def test_a_safelinks_url_without_a_target_is_not_changed():
    url = "https://eur01.safelinks.protection.outlook.com/?data=1"
    assert unwrap(url) == url


def test_a_lookalike_safelinks_host_is_not_unwrapped():
    url = "https://safelinks.protection.outlook.com.evil.example/?url=https%3A%2F%2Fa.com"
    assert unwrap(url) == url


def test_link_text_naming_another_domain_is_mismatched():
    (link,) = extract_links('<a href="http://evil.example/login">https://paypal.com</a>', "")
    assert link.mismatched


def test_the_link_target_is_the_unwrapped_one():
    html = (
        '<a href="https://eur01.safelinks.protection.outlook.com/?url=https%3A%2F%2Fexample.com%2Fx">'
        "example.com</a>"
    )
    (link,) = extract_links(html, "")
    assert link == Link(text="example.com", target="https://example.com/x", mismatched=False)


@pytest.mark.parametrize(
    "html",
    [
        '<a href="https://t.shop.example/c?id=1">Read more</a>',
        '<a href="https://t.shop.example/c"><img src="x.png"></a>',
        '<a href="https://www.example.com/a">example.com</a>',
        '<a href="https://click.example.com/a">www.example.com</a>',
        '<a href="https://t.shop.example/c">Download invoice.pdf</a>',
        '<a href="https://t.shop.example/c">Version 2.0 is out. See what is new.</a>',
        '<a href="https://t.shop.example/c">write to jo@example.com</a>',
        '<a href="mailto:jo@example.com">jo@example.com</a>',
        '<a href="tel:+40123">+40 123</a>',
        '<a href="#top">example.com</a>',
        "<a>example.com</a>",
    ],
)
def test_ordinary_links_are_not_mismatched(html):
    assert not any(link.mismatched for link in extract_links(html, ""))


def test_a_lookalike_subdomain_trick_is_mismatched():
    (link,) = extract_links('<a href="https://paypal.com.evil.example/">paypal.com</a>', "")
    assert link.mismatched


def test_a_text_host_that_only_ends_the_target_with_no_dot_is_mismatched():
    (link,) = extract_links('<a href="https://evilpaypal.com/">paypal.com</a>', "")
    assert link.mismatched


def test_bare_urls_in_the_text_part_are_links_that_never_mismatch():
    (link,) = extract_links(None, "See https://example.com/a, and then more.")
    assert link == Link(
        text="https://example.com/a", target="https://example.com/a", mismatched=False
    )


def test_a_bare_url_already_linked_in_the_html_is_not_listed_twice():
    links = extract_links('<a href="https://example.com/a">Go</a>', "Go: https://example.com/a")
    assert [link.target for link in links] == ["https://example.com/a"]


def test_no_links_at_all():
    assert extract_links(None, "nothing here") == ()


def test_anchor_text_inside_nested_markup_is_joined():
    (link,) = extract_links('<a href="https://x.example/"><b>Read</b> <i>more</i></a>', "")
    assert link.text == "Read more"


SAFELINKS = "https://eur01.safelinks.protection.outlook.com/?url={}&data=1"


@pytest.mark.parametrize(
    "inner",
    [
        "https://good.example\\@evil.example",
        "https://good.example@evil.example",
    ],
)
def test_a_wrapped_target_with_userinfo_or_backslash_is_not_unwrapped(inner):
    wrapped = SAFELINKS.format(quote(inner, safe=""))
    assert unwrap(wrapped) == wrapped
    assert unwrap(inner) == inner


def test_a_deceptive_wrapped_link_stays_mismatched():
    wrapped = SAFELINKS.format("https%3A%2F%2Fgood.example%40evil.example")
    links = extract_links(f'<a href="{wrapped}">good.example</a>', "")
    assert links[0].mismatched is True


def test_a_bare_deceptive_link_is_mismatched():
    links = extract_links('<a href="https://good.example@evil.example/">good.example</a>', "")
    assert links[0].mismatched is True


def test_an_unparseable_target_is_kept_as_not_mismatched():
    links = extract_links('<a href="http://[::1">x.example</a>', "")
    assert links == (Link(text="x.example", target="http://[::1", mismatched=False),)
