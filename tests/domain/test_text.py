import statistics
import time

from aeca.domain.text import bounded, html_to_text, html_to_text_with_quotes, visible


def test_text_is_bounded_and_control_characters_escaped():
    assert visible("a\x1bb") == "a\\x1bb" and len(bounded("x" * 9000, 8000)) == 8000


def test_short_text_is_not_changed_by_bounded():
    assert bounded("short", 8000) == "short"


def test_newlines_and_tabs_survive_visible():
    assert visible("a\nb\tc") == "a\nb\tc"


def test_windows_line_endings_become_newlines():
    assert visible("a\r\nb") == "a\nb"


def test_a_lone_carriage_return_is_escaped():
    assert visible("a\rb") == "a\\x0db"


def test_delete_and_c1_controls_are_escaped():
    assert visible("a\x7fb\x85c") == "a\\x7fb\\x85c"


def test_a_lone_surrogate_from_an_undecodable_header_becomes_a_replacement_character():
    assert visible("a\udcffb") == "a�b"


def test_ordinary_unicode_is_kept():
    assert visible("Jürgen Müller 日本") == "Jürgen Müller 日本"


def test_html_reduction_drops_script_and_style():
    html = (
        "<html><head><title>T</title><style>p{color:red}</style></head>"
        "<body><script>alert(1)</script><p>Hello</p><p>World</p></body></html>"
    )
    assert html_to_text(html) == "Hello\nWorld"


def test_html_reduction_breaks_lines_at_br_and_decodes_entities():
    assert html_to_text("a<br>b &amp; c &eacute;") == "a\nb & c é"


def test_html_reduction_collapses_whitespace_runs():
    assert html_to_text("<div>  a \n\n   b  </div>") == "a b"


def test_unclosed_script_does_not_leak_its_content():
    assert "secret" not in html_to_text("<p>x</p><script>secret")


def test_bidi_zero_width_tag_and_separator_characters_are_escaped():
    assert visible("\u202e") == "\\u202e"
    assert visible("\u200b") == "\\u200b"
    assert visible("\u2066x\u2069") == "\\u2066x\\u2069"
    assert visible("\ufeff") == "\\ufeff"
    assert visible("\u2028\u2029") == "\\u2028\\u2029"
    assert visible("\U000e0041") == "\\U000e0041"


def test_ordinary_international_text_is_not_changed():
    assert visible("Jürgen Müller \u2013 日本") == "Jürgen Müller \u2013 日本"


def test_a_c1_control_is_written_with_two_hex_digits():
    assert visible("\x85") == "\\x85"


# The manager's HTML-only reply: quoted text must still read as quoted, as in a plain-text reply.


def test_text_inside_a_blockquote_is_quoted_with_a_marker():
    html = (
        "<div dir='ltr'>APPROVE<br>thanks</div>"
        "<div class='gmail_quote'>On Sat, AECA wrote:<br>"
        "<blockquote class='gmail_quote'>Hello,<br>REJECT"
        "<blockquote>nested</blockquote></blockquote>"
        "</div><p>Confidential.</p>"
    )

    assert html_to_text_with_quotes(html) == (
        "APPROVE\nthanks\nOn Sat, AECA wrote:\n> Hello,\n> REJECT\n> nested\nConfidential."
    )


def test_without_a_blockquote_it_reads_as_html_to_text():
    html = "<p>APPROVE</p><script>REJECT</script><div>Fine &amp; good</div>"

    assert html_to_text_with_quotes(html) == html_to_text(html)


# Review minor: the reducer must stay linear. Measured by how the time grows from N to 4N pieces on
# one line, not by a wall-clock bound, so a loaded machine cannot fail it: linear growth gives about
# 4, the quadratic reducer it replaced about 12 at this size (measured). Both runs together take
# about a quarter of a second. One preempted run can still push a single ratio past the limit (8.06
# in 1 of 8 measured, with Docker busy), so the test takes the median of several.

PIECE = "<b>" + "w" * 100 + "</b> "
PIECES = 6_000
LINEAR_GROWTH_LIMIT = 8.0
GROWTH_SAMPLES = 5


def best_time_to_reduce(html: str) -> float:
    timings = []
    for _ in range(3):
        started = time.perf_counter()
        html_to_text(html)
        timings.append(time.perf_counter() - started)
    return min(timings)


def growth_from_n_to_4n() -> float:
    return best_time_to_reduce(PIECE * (4 * PIECES)) / best_time_to_reduce(PIECE * PIECES)


def median_growth_from_n_to_4n() -> float:
    return statistics.median(growth_from_n_to_4n() for _ in range(GROWTH_SAMPLES))


def test_a_large_html_body_reduces_in_linear_time():
    growth = median_growth_from_n_to_4n()

    assert growth < LINEAR_GROWTH_LIMIT, f"the time grew {growth:.1f}x from N to 4N pieces"


def test_the_quoting_reducer_joins_its_lines_the_same_way():
    html = "<blockquote>" + "<b>w</b> " * 3 + "</blockquote><p>end</p>"

    assert html_to_text_with_quotes(html) == "> w w w\nend"
