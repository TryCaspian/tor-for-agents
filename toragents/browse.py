"""Browsing over Tor: an agent's WebFetch.

toragents.agent.fetch returns a raw body. This adds a real browsing surface on
top of the same Tor SOCKS exit: fetch a page (clearnet or .onion, Tor
resolves both), follow redirects, and get back a structured Page with the
status, the raw HTML, readable plain text, and the outbound links. This is
the machine equivalent of what a person does in Tor Browser, minus the
human rendering layer agents do not need.

The HTML-to-text extraction uses only the standard library, so browsing
adds no dependency beyond the SOCKS support httpx already has.
"""
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from urllib.parse import urljoin


class _TextExtractor(HTMLParser):
    """Turn HTML into readable text and collect links. Drops script/style,
    inserts breaks around block elements so words do not run together."""

    _SKIP = {"script", "style", "noscript", "template"}
    _BLOCK = {
        "p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6",
        "section", "article", "header", "footer", "ul", "ol", "table", "pre",
    }

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._skip_depth = 0
        self.links: list[str] = []
        self.title = ""
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        if tag in self._SKIP:
            self._skip_depth += 1
        if tag == "title":
            self._in_title = True
        if tag == "a":
            for k, v in attrs:
                if k == "href" and v:
                    self.links.append(v)
        if tag in self._BLOCK:
            self._parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self._SKIP and self._skip_depth:
            self._skip_depth -= 1
        if tag == "title":
            self._in_title = False
        if tag in self._BLOCK:
            self._parts.append("\n")

    def handle_data(self, data):
        if self._skip_depth:
            return
        if self._in_title:
            self.title += data
        self._parts.append(data)

    def get_text(self) -> str:
        raw = "".join(self._parts)
        # Collapse runs of blank space, keep paragraph breaks readable.
        raw = re.sub(r"[ \t\f\v]+", " ", raw)
        raw = re.sub(r"\n\s*\n\s*", "\n\n", raw)
        return raw.strip()


def html_to_text(html: str):
    """Return (title, text, links) extracted from an HTML string."""
    p = _TextExtractor()
    p.feed(html)
    return p.title.strip(), p.get_text(), p.links


@dataclass
class Page:
    url: str
    status: int
    content_type: str
    html: str
    title: str = ""
    text: str = ""
    links: list = field(default_factory=list)

    def summary(self, limit: int = 2000) -> str:
        head = f"{self.status} {self.content_type} :: {self.title}".strip()
        body = self.text[:limit]
        if len(self.text) > limit:
            body += f"\n... [{len(self.text) - limit} more chars]"
        return f"{head}\n\n{body}"


class TorBrowser:
    """Browse the web through a TorNode's SOCKS exit. Handles clearnet and
    .onion identically, because Tor resolves both over SOCKS."""

    def __init__(self, tor, timeout: float = 60.0, max_bytes: int = 5_000_000,
                 isolation: str | None = None):
        self._tor = tor
        self._timeout = timeout
        self._max_bytes = max_bytes
        self._iso = isolation

    def open(self, url: str, resolve_links: bool = True) -> Page:
        with self._tor.http_client(timeout=self._timeout, isolation=self._iso) as client:
            resp = client.get(url, follow_redirects=True)
        ctype = resp.headers.get("content-type", "").split(";")[0].strip()
        body = resp.text[: self._max_bytes]
        title, text, links = "", "", []
        if "html" in ctype or (not ctype and "<html" in body[:2000].lower()):
            title, text, raw_links = html_to_text(body)
            links = (
                [urljoin(str(resp.url), l) for l in raw_links]
                if resolve_links
                else raw_links
            )
        else:
            text = body  # plain text, json, etc: hand it back as-is
        return Page(
            url=str(resp.url),
            status=resp.status_code,
            content_type=ctype or "text/plain",
            html=body,
            title=title,
            text=text,
            links=links,
        )

    def text(self, url: str) -> str:
        """Just the readable text, the way WebFetch hands back a page."""
        return self.open(url, resolve_links=False).text
