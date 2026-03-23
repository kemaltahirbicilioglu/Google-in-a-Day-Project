"""HTML parser using only the standard library html.parser module.

Extracts visible text content and absolute hyperlinks from raw HTML.
"""

import re
from collections import Counter
from html.parser import HTMLParser as _StdlibHTMLParser
from typing import NamedTuple
from urllib.parse import urljoin, urlparse


class ParseResult(NamedTuple):
    """Container for parsed HTML output."""
    word_frequencies: dict[str, int]
    links: list[str]


_SKIP_TAGS = frozenset({"script", "style", "noscript", "svg", "head"})

_WORD_RE = re.compile(r"\b[a-zA-Z]{2,}\b")


class _ContentParser(_StdlibHTMLParser):
    """Accumulates visible text and href links from HTML."""

    def __init__(self) -> None:
        super().__init__()
        self._text_chunks: list[str] = []
        self._links: list[str] = []
        self._skip_depth: int = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _SKIP_TAGS:
            self._skip_depth += 1
        if tag == "a":
            for name, value in attrs:
                if name == "href" and value:
                    self._links.append(value)

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_TAGS and self._skip_depth > 0:
            self._skip_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._skip_depth == 0:
            self._text_chunks.append(data)

    @property
    def text(self) -> str:
        return " ".join(self._text_chunks)

    @property
    def raw_links(self) -> list[str]:
        return self._links


def parse_html(html: str, base_url: str) -> ParseResult:
    """Parse HTML content and return word frequencies and absolute links.

    Args:
        html: Raw HTML string.
        base_url: The URL the HTML was fetched from, used to resolve relative links.

    Returns:
        ParseResult with word_frequencies (Counter) and a deduplicated list of
        absolute HTTP(S) links.
    """
    parser = _ContentParser()
    try:
        parser.feed(html)
    except Exception:
        pass

    words = _WORD_RE.findall(parser.text.lower())
    word_freq: dict[str, int] = dict(Counter(words))

    seen: set[str] = set()
    links: list[str] = []
    for raw_href in parser.raw_links:
        absolute = urljoin(base_url, raw_href)
        parsed = urlparse(absolute)
        if parsed.scheme not in ("http", "https"):
            continue
        clean = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
        if parsed.query:
            clean += f"?{parsed.query}"
        if clean not in seen:
            seen.add(clean)
            links.append(clean)

    return ParseResult(word_frequencies=word_freq, links=links)
