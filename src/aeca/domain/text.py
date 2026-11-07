"""Makes untrusted text safe to show a person or a model: bounded, with nothing invisible."""

import re
import unicodedata
from html.parser import HTMLParser

REPLACEMENT_CHARACTER = "�"
KEPT_CONTROLS = frozenset("\n\t")
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
    """Writes control characters as \\xNN, keeping newlines and tabs, so a pasted escape sequence
    can't act on a terminal or hide text from a reader."""
    return "".join(_visible_character(character) for character in text.replace("\r\n", "\n"))


def _visible_character(character: str) -> str:
    if _is_lone_surrogate(character):
        return REPLACEMENT_CHARACTER
    if unicodedata.category(character) == "Cc" and character not in KEPT_CONTROLS:
        return f"\\x{ord(character):02x}"
    return character


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
