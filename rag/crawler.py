"""
Website crawler using requests + BeautifulSoup.

Starts from a seed URL, follows internal links up to MAX_DEPTH levels,
extracts the main readable text from each page (stripping nav, header,
footer, scripts, ads, etc.).

Returns a list of PageResult objects: {url, title, text}.

Design decisions:
  - Pure requests — no Playwright. gnxtsystems.com is server-rendered.
  - Respects robots.txt by using a normal UA and not hammering the server.
  - Skips non-HTML resources (PDF, images, etc.).
  - Deduplicates by normalised URL (strips query/fragment for link following,
    but keeps the full URL in metadata).
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Callable, List, Optional, Set
from urllib.parse import urljoin, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup, Comment

logger = logging.getLogger("prospect.rag.crawler")

MAX_DEPTH   = 3
POLITE_DELAY = 0.5   # seconds between requests
TIMEOUT_SEC  = 30

_SKIP_EXTENSIONS = {
    ".pdf", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp",
    ".mp4", ".mp3", ".zip", ".doc", ".docx", ".xls", ".xlsx",
    ".ico", ".woff", ".woff2", ".ttf", ".css", ".js",
}

_BOILERPLATE_TAGS = {
    "nav", "header", "footer", "aside", "form", "script",
    "style", "noscript", "iframe", "button", "select", "input",
    "textarea", "meta", "link", "head",
}

_BOILERPLATE_ATTRS = [
    ("role",      {"navigation", "banner", "contentinfo", "complementary", "search"}),
    ("id",        {"nav", "header", "footer", "sidebar", "menu", "cookie", "popup", "modal", "ad"}),
    ("class",     {"nav", "navigation", "navbar", "header", "footer", "sidebar",
                   "menu", "cookie", "popup", "modal", "ad", "advertisement",
                   "breadcrumb", "social", "share", "related", "comment",
                   "widget", "banner"}),
]


@dataclass
class PageResult:
    url:   str
    title: str
    text:  str


class WebsiteCrawler:
    def __init__(
        self,
        seed_url: str,
        max_depth: int = MAX_DEPTH,
        polite_delay: float = POLITE_DELAY,
    ) -> None:
        parsed = urlparse(seed_url)
        self._origin   = f"{parsed.scheme}://{parsed.netloc}"
        self._netloc   = parsed.netloc
        self._seed     = seed_url
        self._max_depth = max_depth
        self._delay    = polite_delay

        self._session = requests.Session()
        self._session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (compatible; ProspectPlatformBot/1.0; "
                "+https://gnxtsystems.com)"
            ),
            "Accept-Language": "en-US,en;q=0.9",
        })

    def crawl(
        self,
        on_page: Optional[Callable[[int, int], None]] = None,
    ) -> List[PageResult]:
        """
        `on_page(pages_fetched, queue_remaining)` is called after each page is
        fetched (whether or not it yielded usable text), giving callers a real,
        live signal for progress reporting during the crawl phase.
        """
        visited: Set[str] = set()
        queue: List[tuple[str, int]] = [(self._seed, 0)]
        results: List[PageResult] = []

        while queue:
            url, depth = queue.pop(0)
            norm = _normalise(url)
            if norm in visited:
                continue
            visited.add(norm)

            page, soup = self._fetch_page_with_soup(url)
            if page is None or soup is None:
                if on_page is not None:
                    on_page(len(results), len(queue))
                continue

            if page.text.strip():
                results.append(page)
                logger.info("[CRAWL] (%d) %s  (%d chars)", depth, url, len(page.text))

            if depth < self._max_depth:
                links = self._extract_internal_links_from_soup(url, soup)
                for link in links:
                    if _normalise(link) not in visited:
                        queue.append((link, depth + 1))

            if on_page is not None:
                on_page(len(results), len(queue))

            time.sleep(self._delay)

        logger.info("[CRAWL] Done. %d page(s) fetched.", len(results))
        return results

    # ------------------------------------------------------------------

    def _fetch_page_with_soup(self, url: str) -> tuple[PageResult | None, BeautifulSoup | None]:
        try:
            resp = self._session.get(url, timeout=TIMEOUT_SEC, allow_redirects=True)
        except Exception as exc:
            logger.warning("[CRAWL] GET failed %s: %s", url, exc)
            return None, None

        if resp.status_code != 200:
            logger.debug("[CRAWL] HTTP %d for %s", resp.status_code, url)
            return None, None

        ct = resp.headers.get("Content-Type", "")
        if "text/html" not in ct:
            logger.debug("[CRAWL] Skipping non-HTML (%s) %s", ct, url)
            return None, None

        try:
            soup = BeautifulSoup(resp.text, "lxml")
        except Exception:
            soup = BeautifulSoup(resp.text, "html.parser")

        title = _extract_title(soup)
        # Re-parse for text extraction — _extract_text uses decompose() which
        # mutates the tree; we need the original soup intact for link extraction.
        try:
            soup_for_text = BeautifulSoup(resp.text, "lxml")
        except Exception:
            soup_for_text = BeautifulSoup(resp.text, "html.parser")
        text = _extract_text(soup_for_text)

        if len(text.split()) < 20:
            return None, soup

        return PageResult(url=resp.url, title=title, text=text), soup

    def _extract_internal_links_from_soup(self, base_url: str, soup: BeautifulSoup) -> List[str]:
        links: List[str] = []
        for a in soup.find_all("a", href=True):
            href = a["href"].strip()
            if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
                continue
            full = urljoin(base_url, href)
            parsed = urlparse(full)
            if parsed.netloc != self._netloc:
                continue
            path_lower = parsed.path.lower()
            if any(path_lower.endswith(ext) for ext in _SKIP_EXTENSIONS):
                continue
            clean = urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", "", ""))
            links.append(clean)
        return links


# ---------------------------------------------------------------------------
# HTML → clean text
# ---------------------------------------------------------------------------

def _extract_title(soup: BeautifulSoup) -> str:
    og = soup.find("meta", property="og:title")
    if og and og.get("content"):
        return og["content"].strip()
    if soup.title and soup.title.string:
        return soup.title.string.strip()
    h1 = soup.find("h1")
    if h1:
        return h1.get_text(" ", strip=True)
    return ""


def _extract_text(soup: BeautifulSoup) -> str:
    # Remove HTML comments
    for comment in soup.find_all(string=lambda t: isinstance(t, Comment)):
        comment.extract()

    # Remove boilerplate tags
    for tag in _BOILERPLATE_TAGS:
        for el in soup.find_all(tag):
            el.decompose()

    # Remove boilerplate by attribute
    for attr, bad_values in _BOILERPLATE_ATTRS:
        for el in soup.find_all(attrs={attr: True}):
            if not el.attrs:
                continue
            val = el.get(attr) or ""
            vals = val if isinstance(val, list) else [v.lower() for v in val.split()]
            if any(v in bad_values for v in vals):
                el.decompose()

    # Prefer <main> or <article> if present
    main = soup.find("main") or soup.find("article") or soup.find(id="main") or soup.find(id="content")
    root = main if main else soup.find("body") or soup

    lines = []
    for el in root.descendants:
        if not hasattr(el, "name"):
            text = el.strip()
            if text:
                lines.append(text)
        elif el.name in ("h1", "h2", "h3", "h4", "p", "li", "td", "th", "dt", "dd"):
            t = el.get_text(" ", strip=True)
            if t:
                lines.append(t)

    # Deduplicate consecutive identical lines and join
    deduped = []
    prev = None
    for line in lines:
        if line != prev:
            deduped.append(line)
            prev = line

    return "\n".join(deduped)


def _normalise(url: str) -> str:
    """Strip fragment + trailing slash for deduplication."""
    p = urlparse(url)
    path = p.path.rstrip("/") or "/"
    return urlunparse((p.scheme, p.netloc, path, p.params, p.query, "")).lower()
