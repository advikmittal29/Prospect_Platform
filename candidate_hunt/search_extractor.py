from __future__ import annotations

import html
import re
from typing import Callable, Dict, List, Optional, Tuple
from urllib.parse import quote_plus, urlparse, urlunparse

from playwright.sync_api import Page

from candidate_hunt.schemas import CandidateSearchCard
from research.linkedin_browser import detect_linkedin_auth_screen
from utils.logging import build_logger

logger = build_logger("prospect.candidate_hunt.search")


class LinkedInCandidateSearchExtractor:
    """
    Candidate ingestion from LinkedIn people search using stable selectors only.
    """

    MAIN_SELECTOR = "div[role='main'][data-sdui-screen*='SearchResultsPeople']"
    NEXT_SELECTOR = "button[data-testid='pagination-controls-next-button-visible']"
    PREV_SELECTOR = "button[data-testid='pagination-controls-prev-button-visible']"
    PAGE_LIST_SELECTOR = "ul[data-testid='pagination-controls-list']"
    PAGE_INDICATOR_SELECTOR = "button[data-testid^='pagination-indicator-']"

    _NAME_BANNED_TOKENS = {
        "connect",
        "message",
        "follow",
        "profile",
        "current",
        "past",
        "mutual",
        "connection",
        "connections",
        "hiring",
        "open",
        "work",
        "developer",
        "engineer",
        "manager",
        "intern",
        "student",
        "recruiter",
        "india",
        "remote",
    }

    def __init__(self, page: Page, auth_handler: Optional[Callable[[], bool]] = None) -> None:
        self.page = page
        self._auth_handler = auth_handler
        self._navigation_timeout_ms = 25000
        self._page_settle_ms = 900
        self._polite_delay_sec = 0.0

    def configure_timing(
        self,
        *,
        navigation_timeout_ms: int,
        page_settle_ms: int,
        polite_delay_sec: float = 0.0,
    ) -> None:
        self._navigation_timeout_ms = max(5000, int(navigation_timeout_ms))
        self._page_settle_ms = max(100, int(page_settle_ms))
        self._polite_delay_sec = max(0.0, float(polite_delay_sec))

    def build_people_search_url(self, query: str) -> str:
        return f"https://www.linkedin.com/search/results/people/?keywords={quote_plus(query)}"

    def collect(
        self,
        search_query: str,
        max_pages: int,
        max_candidates: int,
    ) -> Tuple[List[CandidateSearchCard], List[str]]:
        search_url = self.build_people_search_url(search_query)
        notes: List[str] = []

        self._goto_with_auth(search_url, timeout=self._navigation_timeout_ms)
        self._wait_people_search_ready(notes)

        all_cards: List[CandidateSearchCard] = []
        seen_urls: set[str] = set()

        page_no = self._current_page_no() or 1
        visited = 0

        while visited < max_pages and len(all_cards) < max_candidates:
            visited += 1
            extracted = self._extract_page_cards(page_no=page_no, source_url=self.page.url)
            if not extracted:
                notes.append(f"no_cards_on_page:{page_no}")

            for card in extracted:
                key = card.linkedin_profile_url.lower().rstrip("/")
                if key in seen_urls:
                    continue
                seen_urls.add(key)
                all_cards.append(card)
                if len(all_cards) >= max_candidates:
                    break

            if len(all_cards) >= max_candidates:
                break

            moved, reason = self._go_next_page()
            if not moved:
                notes.append(reason)
                break
            page_no = self._current_page_no() or (page_no + 1)
            if self._polite_delay_sec > 0:
                self.page.wait_for_timeout(int(self._polite_delay_sec * 1000))

        return all_cards[:max_candidates], notes

    def _extract_page_cards(self, page_no: int, source_url: str) -> List[CandidateSearchCard]:
        raw = self.page.evaluate(
            r"""
            () => {
              function clean(v) { return (v || '').replace(/\s+/g, ' ').trim(); }
              function uniq(values) {
                const out = [];
                const seen = new Set();
                for (const v of values || []) {
                  const x = clean(v);
                  if (!x) continue;
                  const key = x.toLowerCase();
                  if (seen.has(key)) continue;
                  seen.add(key);
                  out.push(x);
                }
                return out;
              }

              const root = document.querySelector("div[role='main'][data-sdui-screen*='SearchResultsPeople']")
                || document.querySelector("div[role='main']")
                || document;

              const out = [];
              let globalPos = 0;

              const lists = [...root.querySelectorAll("[role='list']")];
              for (const list of lists) {
                const items = [...list.querySelectorAll("[role='listitem']")];
                for (const item of items) {
                  const candidateAnchors = [...item.querySelectorAll("a[href*='/in/']")];
                  let primary = null;

                  for (const a of candidateAnchors) {
                    const href = a.getAttribute("href") || "";
                    if (!href.includes("/in/")) continue;
                    if (href.includes("/preload/search-custom-invite/")) continue;
                    primary = a;
                    break;
                  }

                  if (!primary) continue;

                  const href = primary.href || primary.getAttribute("href") || "";
                  if (!href || !href.includes("/in/")) continue;

                  globalPos += 1;

                  const nameCandidates = [];
                  nameCandidates.push(primary.getAttribute("aria-label") || "");
                  nameCandidates.push(primary.getAttribute("title") || "");
                  nameCandidates.push(primary.innerText || "");

                  const anchorNameBits = [
                    ...primary.querySelectorAll("span[aria-hidden='true'], span[dir='ltr'], strong"),
                  ].slice(0, 10);
                  for (const node of anchorNameBits) {
                    nameCandidates.push(node.innerText || "");
                  }

                  const itemNameBits = [
                    ...item.querySelectorAll("span[aria-hidden='true'], span[dir='ltr']"),
                  ].slice(0, 12);
                  for (const node of itemNameBits) {
                    nameCandidates.push(node.innerText || "");
                  }

                  const lines = clean(item.innerText || "").split(/\n+/).map(clean).filter(Boolean);
                  const cardLines = uniq(lines).slice(0, 24);
                  const filtered = cardLines.filter((ln) => {
                    const low = ln.toLowerCase();
                    if (!ln) return false;
                    if (/\b(connect|message|follow|view profile|more|inmail|mutual)\b/i.test(low)) return false;
                    if (/\b(1st|2nd|3rd)\b.*\bconnection\b/i.test(low)) return false;
                    if (/^show all/i.test(low)) return false;
                    return true;
                  });

                  let headline = null;
                  let locationText = null;
                  let currentSummary = null;
                  let connectionDegree = null;

                  for (const ln of filtered) {
                    const low = ln.toLowerCase();
                    if (!connectionDegree && /\b(1st|2nd|3rd)\b/.test(low) && /\bconnection\b/.test(low)) {
                      connectionDegree = ln;
                      continue;
                    }
                    if (!currentSummary && (low.startsWith("current:") || low.startsWith("past:") || /\b(current|past):/i.test(low))) {
                      currentSummary = ln;
                      continue;
                    }
                    if (
                      !locationText &&
                      (
                        (/,/.test(ln) && ln.length <= 90 && !/\b(current|past)\b/i.test(low)) ||
                        (/\b(india|united states|usa|uk|canada|australia|germany|france|singapore|uae|remote)\b/i.test(low) && ln.length <= 90)
                      )
                    ) {
                      locationText = ln;
                      continue;
                    }
                    if (
                      !headline &&
                      ln.length >= 8 &&
                      ln.length <= 180 &&
                      /[a-z]/i.test(ln) &&
                      !/\b(current|past)\b:/i.test(low) &&
                      !/\b(1st|2nd|3rd)\b.*\bconnection\b/i.test(low)
                    ) {
                      headline = ln;
                    }
                  }

                  if (!headline && filtered.length > 0) {
                    headline = filtered[0];
                  }

                  const isOpenToWork = item.querySelector("img[alt*='open to work' i]") !== null;

                  out.push({
                    href,
                    full_name: nameCandidates[0] || null,
                    name_candidates: uniq(nameCandidates).slice(0, 14),
                    card_lines: filtered.slice(0, 16),
                    headline: headline || null,
                    location_text: locationText || null,
                    current_summary_text: currentSummary || null,
                    connection_degree: connectionDegree || null,
                    is_open_to_work: !!isOpenToWork,
                    position_on_page: globalPos,
                  });
                }
              }

              return out;
            }
            """
        )

        cards: List[CandidateSearchCard] = []
        for item in raw or []:
            canon = self._canonical_profile_url(item.get("href"))
            if not canon:
                continue
            public_id = self._extract_public_id(canon)
            full_name = self._resolve_full_name(
                name_candidates=item.get("name_candidates"),
                fallback_name=item.get("full_name"),
                card_lines=item.get("card_lines"),
                public_id=public_id,
            )
            cards.append(
                CandidateSearchCard(
                    linkedin_profile_url=canon,
                    linkedin_public_id=public_id,
                    full_name=full_name,
                    headline=self._normalize_headline(item.get("headline"), full_name),
                    location_text=self._normalize_location(item.get("location_text")),
                    current_summary_text=self._normalize_summary(item.get("current_summary_text")),
                    connection_degree=self._clean(item.get("connection_degree")),
                    is_open_to_work=bool(item.get("is_open_to_work")),
                    search_page_no=page_no,
                    position_on_page=int(item.get("position_on_page") or 0),
                    source_search_url=source_url,
                )
            )
        return cards

    def _wait_people_search_ready(self, notes: List[str]) -> None:
        try:
            self.page.wait_for_selector(self.MAIN_SELECTOR, timeout=15000)
        except Exception:
            notes.append("missing_people_search_main_root")

    def _current_page_no(self) -> Optional[int]:
        try:
            current = self.page.locator(
                "button[data-testid^='pagination-indicator-'][aria-current='true']"
            )
            if current.count() > 0:
                txt = (current.first.inner_text(timeout=800) or "").strip()
                match = re.search(r"\d+", txt)
                if match:
                    return int(match.group(0))
        except Exception:
            pass
        return None

    def _go_next_page(self) -> Tuple[bool, str]:
        try:
            next_btn = self.page.locator(self.NEXT_SELECTOR)
            if next_btn.count() == 0:
                return False, "next_button_not_found"

            btn = next_btn.first
            disabled_attr = (btn.get_attribute("aria-disabled") or "").strip().lower()
            if disabled_attr == "true":
                return False, "next_button_disabled"
            if btn.is_disabled():
                return False, "next_button_disabled"

            current_page = self._current_page_no()
            btn.click(timeout=5000)
            self._settle()

            new_page = self._current_page_no()
            if current_page is not None and new_page is not None and new_page == current_page:
                return False, "next_click_no_page_change"
            return True, "next_page_ok"
        except Exception as exc:
            return False, f"next_page_error:{type(exc).__name__}"

    def _goto_with_auth(self, url: str, timeout: int) -> None:
        for attempt in range(1, 3):
            self.page.goto(url, wait_until="domcontentloaded", timeout=timeout)
            self._settle()
            is_auth, reason = detect_linkedin_auth_screen(self.page)
            if not is_auth:
                return
            if not self._auth_handler:
                raise RuntimeError(f"LinkedIn auth required while opening {url}: {reason}")
            logger.warning(
                "Auth wall detected while opening %s (attempt %d): %s",
                url,
                attempt,
                reason,
            )
            if not self._auth_handler():
                raise RuntimeError(f"LinkedIn login failed for auth wall: {reason}")
        raise RuntimeError(f"Could not pass auth wall for {url} after login retries.")

    def _settle(self) -> None:
        try:
            self.page.wait_for_load_state("networkidle", timeout=2500)
        except Exception:
            pass
        self.page.wait_for_timeout(self._page_settle_ms)

    def _canonical_profile_url(self, url: Optional[str]) -> Optional[str]:
        if not url:
            return None
        x = str(url).strip()
        if not x:
            return None

        parsed = urlparse(x)
        if not parsed.netloc:
            if x.startswith("/in/"):
                x = f"https://www.linkedin.com{x}"
                parsed = urlparse(x)
            else:
                return None

        if "/in/" not in parsed.path:
            return None

        path = parsed.path
        match = re.search(r"/in/([^/\?#]+)/?", path)
        if not match:
            return None
        slug = match.group(1)
        clean_path = f"/in/{slug}/"

        return urlunparse((parsed.scheme or "https", parsed.netloc, clean_path, "", "", ""))

    def _extract_public_id(self, profile_url: str) -> Optional[str]:
        match = re.search(r"/in/([^/\?#]+)/?", profile_url)
        if not match:
            return None
        return match.group(1).strip() or None

    def _clean(self, value: object) -> Optional[str]:
        if value is None:
            return None
        text = html.unescape(re.sub(r"\s+", " ", str(value))).strip()
        return text or None

    def _as_string_list(self, value: object) -> List[str]:
        if isinstance(value, list):
            out: List[str] = []
            for item in value:
                clean = self._clean(item)
                if clean:
                    out.append(clean)
            return out
        clean = self._clean(value)
        return [clean] if clean else []

    def _resolve_full_name(
        self,
        *,
        name_candidates: object,
        fallback_name: object,
        card_lines: object,
        public_id: Optional[str],
    ) -> Optional[str]:
        raw_candidates = self._as_string_list(name_candidates)
        raw_candidates.extend(self._as_string_list(fallback_name))
        raw_candidates.extend(self._as_string_list(card_lines)[:4])

        scored: List[Tuple[int, str]] = []
        seen: set[str] = set()
        for raw in raw_candidates:
            norm = self._normalize_full_name(raw)
            if not norm:
                continue
            key = norm.lower()
            if key in seen:
                continue
            seen.add(key)
            score = self._name_quality_score(norm)
            if score < 0:
                continue
            scored.append((score, norm))

        if scored:
            scored.sort(key=lambda item: (item[0], -len(item[1])), reverse=True)
            return scored[0][1][:255]

        return self._name_from_public_id(public_id)

    def _normalize_full_name(self, value: object) -> Optional[str]:
        text = self._clean(value)
        if not text:
            return None

        # Common aria-label pattern: "View John Doe's profile"
        m = re.search(r"\bview\s+(.+?)[\'\u2019]s\s+profile\b", text, flags=re.I)
        if m:
            text = m.group(1)

        text = re.sub(r"^(view|message|connect with|connect|follow|invite)\s+", "", text, flags=re.I)

        cut_patterns = [
            r"\b(mutual connection|mutual connections)\b",
            r"\b(current|past)\s*:",
            r"\b(connect|message|follow|inmail)\b",
            r"\bopen to work\b",
            r"\b\d+(st|nd|rd)\b.*\bconnection\b",
        ]
        lowered = text.lower()
        cut_at = None
        for pattern in cut_patterns:
            found = re.search(pattern, lowered, flags=re.I)
            if found:
                cut_at = found.start() if cut_at is None else min(cut_at, found.start())
        if cut_at is not None and cut_at > 0:
            text = text[:cut_at]

        # Split on visual separators that often join name + headline.
        text = re.split(r"\s(?:\||\u2022|\u00b7|\u2023|\u2043|/)\s", text, maxsplit=1)[0]
        text = re.split(r"\s[\u2013\u2014]\s", text, maxsplit=1)[0]

        # Remove brackets and non-name symbols.
        text = re.sub(r"[\(\)\[\]\{\}<>\"]", " ", text)
        text = re.sub(r"[^A-Za-z\u00C0-\u024F\u1E00-\u1EFF\u0400-\u04FF\'\.\-\s]", " ", text)
        text = re.sub(r"\s+", " ", text).strip(" .,-:;")
        if not text:
            return None

        return text[:255]

    def _name_quality_score(self, value: str) -> int:
        text = value.strip()
        if not self._looks_like_name(text):
            return -100

        words = [w for w in text.split() if w]
        score = 0

        if 2 <= len(words) <= 4:
            score += 8
        elif len(words) == 1:
            score += 2
        elif len(words) <= 6:
            score += 4
        else:
            score -= 6

        if 6 <= len(text) <= 40:
            score += 3

        titleish = sum(1 for w in words if w[:1].isupper())
        if titleish >= max(1, len(words) - 1):
            score += 2

        if any("'" in w or "-" in w for w in words):
            score += 1

        return score

    def _looks_like_name(self, value: str) -> bool:
        text = value.strip()
        if len(text) < 2 or len(text) > 80:
            return False
        if any(ch.isdigit() for ch in text):
            return False

        words = [w.strip(".'-") for w in text.split() if w.strip(".'-")]
        if len(words) < 2 or len(words) > 6:
            return False

        lowered = text.lower()
        if any(token in lowered for token in ("current:", "past:", " at ", "open to work")):
            return False

        for token in words:
            if token.lower() in self._NAME_BANNED_TOKENS:
                return False

        letters = sum(1 for ch in text if ch.isalpha())
        return letters >= max(2, len(text) // 3)

    def _name_from_public_id(self, public_id: Optional[str]) -> Optional[str]:
        if not public_id:
            return None
        slug = public_id.strip().strip("/")
        if not slug:
            return None
        slug = re.sub(r"[-_]*\d+$", "", slug)
        tokens = [t for t in re.split(r"[-_]+", slug) if t and not re.search(r"\d", t)]
        tokens = [t for t in tokens if t.lower() not in {"linkedin", "profile", "official"}]
        if len(tokens) < 2:
            return None
        candidate = " ".join(t.capitalize() for t in tokens[:4]).strip()
        return candidate if self._looks_like_name(candidate) else None

    def _normalize_headline(self, value: object, full_name: Optional[str]) -> Optional[str]:
        text = self._clean(value)
        if not text:
            return None
        low = text.lower()
        if full_name and low == full_name.lower():
            return None
        if re.search(r"\b(connect|message|follow|mutual connection|view profile|inmail)\b", low):
            return None
        if len(text) < 5:
            return None
        return text[:500]

    def _normalize_location(self, value: object) -> Optional[str]:
        text = self._clean(value)
        if not text:
            return None
        low = text.lower()
        if re.search(r"\b(connect|message|follow|mutual connection|view profile)\b", low):
            return None
        if len(text) > 120:
            return None
        return text[:255]

    def _normalize_summary(self, value: object) -> Optional[str]:
        text = self._clean(value)
        if not text:
            return None
        low = text.lower()
        if re.search(r"\b(connect|message|follow|mutual connection|view profile)\b", low):
            return None
        if len(text) < 4:
            return None
        return text[:500]
