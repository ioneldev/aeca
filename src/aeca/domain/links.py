"""Finds the links in an email and spots the ones that say one place and go to another."""

import re
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import parse_qs, urlsplit

SAFELINKS_SUFFIX = ".safelinks.protection.outlook.com"
URLDEFENSE_V3 = re.compile(r"^https?://urldefense\.com/v3/__(.+)__;", re.IGNORECASE)
BARE_URL = re.compile(r"https?://[^\s<>\"'\])]+", re.IGNORECASE)
URL_TRAILING_PUNCTUATION = ".,;:!?"
HOSTNAME_IN_TEXT = re.compile(
    r"(?<![@\w.-])(?:https?://)?((?:[a-z0-9-]+\.)+[a-z]{2,})(?![\w-])", re.IGNORECASE
)
# Link text such as "invoice.pdf" looks like a hostname but names a file.
FILE_EXTENSIONS = frozenset(
    {"pdf", "doc", "docx", "xls", "xlsx", "ppt", "pptx", "txt", "csv", "zip", "png", "jpg", "jpeg",
     "gif", "svg", "html", "htm", "ics", "xml", "json", "mp3", "mp4", "mov"}
)  # fmt: skip
MAX_UNWRAP_LAYERS = 3


@dataclass(frozen=True, slots=True)
class Link:
    text: str
    target: str
    mismatched: bool


def unwrap(url: str) -> str:
    """Strips the rewrite a corporate mail filter put around a link, so the real host shows."""
    for _ in range(MAX_UNWRAP_LAYERS):
        inner = _unwrap_once(url)
        if inner == url:
            return url
        url = inner
    return url


def _unwrap_once(url: str) -> str:
    host = (urlsplit(url).hostname or "").lower()
    if host.endswith(SAFELINKS_SUFFIX):
        targets = parse_qs(urlsplit(url).query).get("url")
        return targets[0] if targets else url
    defended = URLDEFENSE_V3.match(url)
    return defended.group(1) if defended else url


def extract_links(html: str | None, text: str) -> tuple[Link, ...]:
    links = _anchor_links(html) if html else []
    already_linked = {link.target for link in links}
    for url in _bare_urls(text):
        if unwrap(url) not in already_linked:
            links.append(Link(text=url, target=unwrap(url), mismatched=False))
            already_linked.add(unwrap(url))
    return tuple(links)


def _bare_urls(text: str) -> list[str]:
    return [match.group(0).rstrip(URL_TRAILING_PUNCTUATION) for match in BARE_URL.finditer(text)]


def _anchor_links(html: str) -> list[Link]:
    collector = _AnchorCollector()
    collector.feed(html)
    collector.close()
    return [_link_from_anchor(text, href) for text, href in collector.anchors]


def _link_from_anchor(text: str, href: str) -> Link:
    target = unwrap(href.strip())
    return Link(text=text, target=target, mismatched=_names_another_host(text, target))


class _AnchorCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.anchors: list[tuple[str, str]] = []
        self._href: str | None = None
        self._text_pieces: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return
        self._finish_anchor()
        self._href = dict(attrs).get("href")
        self._text_pieces = []

    def handle_endtag(self, tag: str) -> None:
        if tag == "a":
            self._finish_anchor()

    def handle_data(self, data: str) -> None:
        if self._href is not None:
            self._text_pieces.append(data)

    def close(self) -> None:
        super().close()
        self._finish_anchor()

    def _finish_anchor(self) -> None:
        if self._href:
            self.anchors.append((" ".join("".join(self._text_pieces).split()), self._href))
        self._href = None
        self._text_pieces = []


def _names_another_host(text: str, target: str) -> bool:
    target_host = _comparable_host(urlsplit(target).hostname)
    if not target_host or urlsplit(target).scheme not in {"http", "https"}:
        return False
    return any(not _same_site(named_host, target_host) for named_host in _hosts_named_in(text))


def _hosts_named_in(text: str) -> list[str]:
    hosts = (_comparable_host(match.group(1)) for match in HOSTNAME_IN_TEXT.finditer(text))
    return [host for host in hosts if host.rpartition(".")[2] not in FILE_EXTENSIONS]


def _comparable_host(host: str | None) -> str:
    return (host or "").lower().removeprefix("www.")


def _same_site(named_host: str, target_host: str) -> bool:
    return target_host == named_host or target_host.endswith("." + named_host)
