"""Makes untrusted text safe to show a person or a model: bounded, with nothing invisible."""

import re
import unicodedata
from html.parser import HTMLParser

REPLACEMENT_CHARACTER = "�"
KEPT_CONTROLS = frozenset("\n\t")
# Cf covers bidi overrides, zero-width characters and tag characters; Zl and Zp are the Unicode
# line and paragraph separators. All of them can hide or reorder text from a reader.
ESCAPED_CATEGORIES = frozenset({"Cc", "Cf", "Zl", "Zp"})
INVISIBLE_ELEMENTS = frozenset({"script", "style", "head"})
LINE_BREAKING_ELEMENTS = frozenset(
    {"br", "p", "div", "li", "tr", "table", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6",
     "blockquote", "pre", "hr", "section", "article", "header", "footer"}
)  # fmt: skip
WHITESPACE_RUN = re.compile(r"\s+")
SPACES_AND_TABS = re.compile(r"[ \t\r\f\v]+")


def bounded(text: str, limit: int) -> str:
    return text[:limit]


def visible(text: str) -> str:
    """Writes control, format and separator characters as \\xNN, \\uXXXX or \\UXXXXXXXX, keeping
    newlines and tabs, so pasted text can't act on a terminal or hide from a reader."""
    return "".join(_visible_character(character) for character in text.replace("\r\n", "\n"))


def _visible_character(character: str) -> str:
    if _is_lone_surrogate(character):
        return REPLACEMENT_CHARACTER
    if unicodedata.category(character) in ESCAPED_CATEGORIES and character not in KEPT_CONTROLS:
        return _escape(character)
    return character


def _escape(character: str) -> str:
    code_point = ord(character)
    if code_point <= 0xFF:
        return f"\\x{code_point:02x}"
    if code_point <= 0xFFFF:
        return f"\\u{code_point:04x}"
    return f"\\U{code_point:08x}"


def _is_lone_surrogate(character: str) -> bool:
    return 0xD800 <= ord(character) <= 0xDFFF


def html_to_text(html: str) -> str:
    reducer = _TextReducer()
    reducer.feed(html)
    reducer.close()
    return reducer.text()


class _TextReducer(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._pieces: list[str] = []
        self._invisible_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in INVISIBLE_ELEMENTS:
            self._invisible_depth += 1
        if tag in LINE_BREAKING_ELEMENTS:
            self._pieces.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in INVISIBLE_ELEMENTS:
            self._invisible_depth = max(0, self._invisible_depth - 1)
        if tag in LINE_BREAKING_ELEMENTS:
            self._pieces.append("\n")

    def handle_data(self, data: str) -> None:
        if self._invisible_depth == 0:
            self._pieces.append(WHITESPACE_RUN.sub(" ", data))

    def text(self) -> str:
        lines = ("".join(self._pieces)).split("\n")
        tidy_lines = (SPACES_AND_TABS.sub(" ", line).strip() for line in lines)
        return "\n".join(line for line in tidy_lines if line)
