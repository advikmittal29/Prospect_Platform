"""
LinkedIn people finder.
Given a company URL, iterates over search keywords and collects
ProspectProfile candidates, ranked by role relevance.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple
from urllib.parse import quote, urlparse, urlunparse

from playwright.sync_api import Page

from research.linkedin_browser import detect_linkedin_auth_screen
from utils.logging import build_logger

logger = build_logger("prospect.research.people_finder")


@dataclass
class ProspectCandidate:
    name: Optional[str]
    profile_url: str
    headline: Optional[str]
    matched_keyword: str
    role_bucket: str
    confidence: int
    connection_degree: Optional[str] = None


@dataclass
class PeopleSearchResult:
    company_url: str
    prospects: List[ProspectCandidate] = field(default_factory=list)
    searched_keywords: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)


class LinkedInPeopleFinder:
    """
    Searches a company's /people/ page with rotating keywords.
    Deduplicates by profile URL, keeps highest-scoring version per person.
    """

    POSITIVE_PATTERNS = [
        (r"\bhead of talent acquisition\b", "ta_leader", 40),
        (r"\bhead of hr\b", "hr_leader", 40),
        (r"\btalent acquisition\b", "recruitment", 35),
        (r"\brecruiter\b", "recruitment", 35),
        (r"\bhr manager\b", "hr", 35),
        (r"\bhr executive\b", "hr", 28),
        (r"\bhuman resources\b", "hr", 28),
        (r"\bhiring manager\b", "hiring_manager", 35),
        (r"\bcto\b", "exec_tech", 35),
        (r"\bchief technology officer\b", "exec_tech", 35),
        (r"\bvp engineering\b", "tech_leader", 32),
        (r"\bdirector of engineering\b", "tech_leader", 30),
        (r"\bengineering manager\b", "tech_leader", 28),
        (r"\bhead of engineering\b", "tech_leader", 30),
        (r"\bceo\b", "exec_business", 30),
        (r"\bfounder\b", "founder", 30),
        (r"\bco[\s-]?founder\b", "founder", 30),
        (r"\bvp of engineering\b", "tech_leader", 32),
        (r"\bdirector of hr\b", "hr_leader", 35),
        (r"\bchief human resources\b", "hr_leader", 40),
        (r"\bpeople operations\b", "hr", 30),
        (r"\bpeople manager\b", "hr", 28),
    ]

    NEGATIVE_PATTERNS = [
        (r"\bintern\b", -30),
        (r"\btrainee\b", -25),
        (r"\bstudent\b", -25),
        (r"\bsoftware engineer\b", -15),
        (r"\bdeveloper\b", -15),
        (r"\btester\b", -15),
        (r"\banalyst\b", -12),
        (r"\bassociate\b", -10),
        (r"\bexecutive assistant\b", -20),
        (r"\baccountant\b", -20),
        (r"\bgraphic designer\b", -20),
    ]

    DECISION_MAKER_BOOST = re.compile(
        r"\bceo\b|\bcto\b|\bfounder\b|\bhead of\b|\bvp\b|\bdirector\b|\bmanager\b",
        re.I,
    )
    RECRUITER_BOOST = re.compile(r"\brecruit|\btalent acquisition\b|\bhiring\b", re.I)

    MIN_CONFIDENCE = 55

    def __init__(
        self,
        page: Page,
        auth_handler: Optional[Callable[[], bool]] = None,
    ) -> None:
        self.page = page
        self._auth_handler = auth_handler

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def collect(
        self,
        company_url: str,
        keywords: List[str],
        max_results: int = 20,
    ) -> PeopleSearchResult:
        company_url = self._canon_company(company_url)
        all_found: Dict[str, ProspectCandidate] = {}
        searched: List[str] = []
        notes: List[str] = []

        for kw in keywords:
            people_url = company_url.rstrip("/") + f"/people/?keywords={quote(kw)}"
            try:
                self._goto_with_auth(people_url, timeout=15000)
            except Exception as exc:
                notes.append(f"nav_failed:{kw}:{exc}")
                continue

            searched.append(kw)
            cards = self._extract_cards(kw)

            for p in cards:
                canon = self._canon_profile(p.profile_url)
                if not canon:
                    continue
                existing = all_found.get(canon)
                if existing is None or p.confidence > existing.confidence:
                    p.profile_url = canon
                    all_found[canon] = p

            if len(all_found) >= max_results:
                break

        ranked = sorted(all_found.values(), key=lambda x: x.confidence, reverse=True)[:max_results]

        return PeopleSearchResult(
            company_url=company_url,
            prospects=ranked,
            searched_keywords=searched,
            notes=notes,
        )

    # ------------------------------------------------------------------
    # Card extraction
    # ------------------------------------------------------------------

    def _extract_cards(self, keyword: str) -> List[ProspectCandidate]:
        try:
            raw = self.page.evaluate("""
            () => {
              function clean(s){return(s||'').replace(/\\s+/g,' ').trim();}
              const cards=[...document.querySelectorAll('.org-people-profile-card__profile-card-spacing section')];
              return cards.map(card=>{
                const anchors=[...card.querySelectorAll('a[href*="linkedin.com/in/"],a[href^="/in/"]')];
                const primary=anchors.length?anchors[0]:null;
                const title=card.querySelector('.artdeco-entity-lockup__title');
                const subtitle=card.querySelector('.artdeco-entity-lockup__subtitle');
                const degree=card.querySelector('.artdeco-entity-lockup__degree');
                return{
                  name:clean(title?title.innerText:''),
                  profile_url:primary?primary.href:null,
                  headline:clean(subtitle?subtitle.innerText:''),
                  connection_degree:clean(degree?degree.innerText:'')
                };
              });
            }
            """)
        except Exception:
            return []

        found: List[ProspectCandidate] = []
        for item in (raw or []):
            url = item.get("profile_url")
            if not url or not self._is_profile_url(url):
                continue
            headline = (item.get("headline") or "").strip()
            name = (item.get("name") or "").strip()
            degree = (item.get("connection_degree") or "").strip() or None

            score, bucket = self._score(headline, keyword)
            if score < self.MIN_CONFIDENCE:
                continue

            found.append(ProspectCandidate(
                name=name or None,
                profile_url=url,
                headline=headline or None,
                matched_keyword=keyword,
                role_bucket=bucket,
                confidence=score,
                connection_degree=degree,
            ))

        return found

    # ------------------------------------------------------------------
    # Scoring
    # ------------------------------------------------------------------

    def _score(self, headline: str, keyword: str) -> Tuple[int, str]:
        text = (headline or "").lower()
        kw = (keyword or "").lower()

        if not text:
            return 0, "unknown"

        score = 0
        bucket = "other"

        if kw and kw in text:
            score += 20

        for pattern, bkt, pts in self.POSITIVE_PATTERNS:
            if re.search(pattern, text, re.I):
                score += pts
                bucket = bkt
                break

        for pattern, penalty in self.NEGATIVE_PATTERNS:
            if re.search(pattern, text, re.I):
                score += penalty

        if self.DECISION_MAKER_BOOST.search(text):
            score += 15
        if self.RECRUITER_BOOST.search(text):
            score += 15

        return max(0, min(100, score)), bucket

    # ------------------------------------------------------------------
    # URL helpers
    # ------------------------------------------------------------------

    def _settle(self) -> None:
        try:
            self.page.wait_for_load_state("networkidle", timeout=2500)
        except Exception:
            pass
        self.page.wait_for_timeout(700)

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

    def _canon_company(self, url: str) -> str:
        p = urlparse(url)
        path = p.path.rstrip("/") + "/"
        return urlunparse((p.scheme, p.netloc, path, "", "", ""))

    def _canon_profile(self, url: str) -> Optional[str]:
        if not self._is_profile_url(url):
            return None
        p = urlparse(url)
        path = p.path.rstrip("/") + "/"
        return urlunparse((p.scheme, p.netloc, path, "", "", ""))

    def _is_profile_url(self, url: str) -> bool:
        x = (url or "").lower()
        return "linkedin.com/in/" in x or x.startswith("/in/")

