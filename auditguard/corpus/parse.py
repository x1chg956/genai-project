"""Raw document → plain text with one line per block, so clause numbers start lines.

HTML: block tags (p, div, li, h1-h6, br, tr) end a line; table cells are joined with " | ",
so a table stays a text table inside its clause. Scripts and styles are dropped.
PDF: page text from pypdf, in reading order. Untrusted content: only text is extracted.
"""

from __future__ import annotations

from html.parser import HTMLParser
from io import BytesIO

_BLOCK = {"p", "div", "li", "h1", "h2", "h3", "h4", "h5", "h6", "br", "tr", "table", "section"}
_CELL = {"td", "th"}
_SKIP = {"script", "style", "noscript"}


class _Text(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.lines: list[str] = []
        self.buf: list[str] = []
        self.skip = 0

    def _flush(self) -> None:
        line = " ".join("".join(self.buf).split())
        if line:
            self.lines.append(line)
        self.buf = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _SKIP:
            self.skip += 1
        elif tag in _BLOCK:
            self._flush()
        elif tag in _CELL and self.buf and "".join(self.buf).strip():
            self.buf.append(" | ")

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP:
            self.skip = max(0, self.skip - 1)
        elif tag in _BLOCK:
            self._flush()

    def handle_data(self, data: str) -> None:
        if not self.skip:
            self.buf.append(data)


def html_to_text(html: str) -> str:
    parser = _Text()
    parser.feed(html)
    parser.close()
    parser._flush()
    return "\n".join(parser.lines)


def pdf_to_text(data: bytes) -> str:
    from pypdf import PdfReader

    reader = PdfReader(BytesIO(data))
    return "\n".join(page.extract_text() or "" for page in reader.pages)
