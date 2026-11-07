from aeca.domain.text import bounded, html_to_text, visible


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
