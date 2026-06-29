"""
LinkedIn company finder.
Given a company name string, searches LinkedIn and returns the best
matching /company/... URL with a confidence score.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import quote_plus, urlparse, urlunparse

from playwright.sync_api import Page

from research.linkedin_browser import detect_linkedin_auth_screen
from utils.logging import build_logger

logger = build_logger("prospect.research.company_finder")


@dataclass
class CompanyBrief:
    company_url: str
    company_name: Optional[str] = None
    tagline: Optional[str] = None
    industry: Optional[str] = None
    location: Optional[str] = None
    employee_range: Optional[str] = None
    followers: Optional[str] = None


@dataclass
class CompanyMatchResult:
    status: str          # success | ambiguous_match | no_results_found | no_company_candidate_found | wrong_page | error
    company_url: Optional[str]
    matched_title: Optional[str]
    confidence: float
    reason: str
    company_brief: Optional[CompanyBrief] = None
    candidates: List[Dict[str, Any]] = field(default_factory=list)


class LinkedInCompanyFinder:
    """
    Operates on a Playwright Page that is already attached and ready.
    Steps:
      1. Navigate to LinkedIn company search URL for the query
      2. Extract and score result cards
      3. Navigate to best candidate and extract company brief
    """

    MIN_CONFIDENCE = 70.0

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

    def find(self, company_name: str) -> CompanyMatchResult:
        """
        Main entry point.
        Returns a CompanyMatchResult. On success, company_brief is populated.
        """
        search_url = (
            "https://www.linkedin.com/search/results/companies/"
            f"?keywords={quote_plus(company_name)}"
        )
        logger.debug("Searching LinkedIn for company: %s", company_name)

        try:
            self._goto_with_auth(search_url, timeout=20000)
        except Exception as exc:
            return CompanyMatchResult(
                status="error",
                company_url=None,
                matched_title=None,
                confidence=0.0,
                reason=f"Navigation failed: {exc}",
            )

        ok, why = self._validate_search_page()
        if not ok:
            return CompanyMatchResult(
                status="wrong_page",
                company_url=None,
                matched_title=None,
                confidence=0.0,
                reason=why,
            )

        raw = self._extract_results()
        if not raw:
            return CompanyMatchResult(
                status="no_results_found",
                company_url=None,
                matched_title=None,
                confidence=0.0,
                reason="No visible search results extracted.",
            )

        norm_query = self._norm(company_name)
        candidates: List[Dict[str, Any]] = []

        for item in raw:
            if self._classify_href(item.get("primary_url", "")) not in {"company", "showcase"}:
                continue
            score, reasons = self._score(item, norm_query)
            candidates.append({
                "title": item["title"],
                "primary_url": self._canonicalize(item["primary_url"]),
                "entity_type": self._classify_href(item["primary_url"]),
                "industry": item.get("industry"),
                "location": item.get("location"),
                "score": score,
                "score_reasons": reasons,
            })

        if not candidates:
            return CompanyMatchResult(
                status="no_company_candidate_found",
                company_url=None,
                matched_title=None,
                confidence=0.0,
                reason="Results found but none were primary /company/ cards.",
                candidates=raw,
            )

        candidates.sort(key=lambda x: x["score"], reverse=True)
        best = candidates[0]

        if best["score"] < self.MIN_CONFIDENCE:
            return CompanyMatchResult(
                status="ambiguous_match",
                company_url=None,
                matched_title=best["title"],
                confidence=float(best["score"]),
                reason="Best candidate below confidence threshold.",
                candidates=candidates,
            )

        company_url = best["primary_url"]
        brief = self._get_company_brief(company_url)

        return CompanyMatchResult(
            status="success",
            company_url=company_url,
            matched_title=best["title"],
            confidence=float(best["score"]),
            reason="Best /company/ card identified.",
            company_brief=brief,
            candidates=candidates,
        )

    def get_company_brief(self, company_url: str) -> Optional[CompanyBrief]:
        """Public brief refresh API for already-known LinkedIn company URLs."""
        if not company_url:
            return None
        return self._get_company_brief(self._canonicalize(company_url))

    # ------------------------------------------------------------------
    # Company brief extraction
    # ------------------------------------------------------------------

    def _get_company_brief(self, company_url: str) -> Optional[CompanyBrief]:
        try:
            self._goto_with_auth(company_url, timeout=15000)
        except Exception as exc:
            logger.warning("Could not load company page %s: %s", company_url, exc)
            return None

        try:
            data = self.page.evaluate("""
            () => {
              function clean(s) { return (s||'').replace(/\\s+/g,' ').trim(); }
              const root = document.querySelector('main[aria-label*="Organization"]') || document;
              const h1 = root.querySelector('h1');
              const taglineEl = root.querySelector('.org-top-card-summary__tagline');
              const items = [...root.querySelectorAll('.org-top-card-summary-info-list__info-item')]
                .map(x=>clean(x.innerText)).filter(Boolean);
              let industry=null,location=null,followers=null,employees=null;
              for(const item of items){
                if(!industry&&/consulting|services|technology|software|financial|health|education|marketing|manufacturing|retail|media|insurance|banking/i.test(item)){industry=item;continue;}
                if(!location&&/,/.test(item)&&!/followers?/i.test(item)&&!/employees?/i.test(item)){location=item;continue;}
                if(!followers&&/followers?/i.test(item)){followers=item;continue;}
                if(!employees&&/employees?/i.test(item)){employees=item;}
              }
              return {
                company_name:clean(h1?h1.innerText:''),
                tagline:clean(taglineEl?taglineEl.innerText:''),
                industry,location,followers,employee_range:employees
              };
            }
            """)
            return CompanyBrief(
                company_url=company_url,
                company_name=data.get("company_name") or None,
                tagline=data.get("tagline") or None,
                industry=data.get("industry") or None,
                location=data.get("location") or None,
                employee_range=data.get("employee_range") or None,
                followers=data.get("followers") or None,
            )
        except Exception as exc:
            logger.warning("Company brief extraction failed for %s: %s", company_url, exc)
            return None

    # ------------------------------------------------------------------
    # Page validation
    # ------------------------------------------------------------------

    def _validate_search_page(self) -> Tuple[bool, str]:
        try:
            url = self.page.url.lower()
        except Exception:
            url = ""
        if "linkedin.com/search/results/companies" in url:
            return True, "URL matches company search."
        try:
            has_filter = self.page.locator("label").filter(has_text="Companies").count() > 0
            if has_filter:
                return True, "DOM has Companies filter."
        except Exception:
            pass
        return False, "Page does not look like LinkedIn company search."

    # ------------------------------------------------------------------
    # Result extraction
    # ------------------------------------------------------------------

    def _extract_results(self) -> List[Dict[str, Any]]:
        try:
            raw = self.page.evaluate("""
            () => {
              function clean(s){return(s||'').replace(/\\s+/g,' ').trim();}
              function isVisible(el){
                if(!el)return false;
                const s=getComputedStyle(el),r=el.getBoundingClientRect();
                return s.display!=='none'&&s.visibility!=='hidden'&&
                       parseFloat(s.opacity||'1')>0.05&&r.width>4&&r.height>4;
              }
              function getRoot(anchor){
                let node=anchor,depth=0;
                while(node&&depth<8){
                  const t=clean(node.innerText||''),r=node.getBoundingClientRect();
                  if(t.length>20&&r.width>100&&r.height>40&&(node.getAttribute('role')==='listitem'||node.querySelector('img,p,div,a'))){return node;}
                  node=node.parentElement;depth++;
                }
                return anchor;
              }
              const anchors=[...document.querySelectorAll('a[href]')]
                .filter(a=>isVisible(a))
                .filter(a=>{const h=a.href||'';return h.includes('/company/')||h.includes('/showcase/');});
              const seen=new Set(),results=[];
              for(const a of anchors){
                const href=a.href||'',title=clean(a.innerText||'');
                if(!href||!title)continue;
                const key=href+'||'+title;
                if(seen.has(key))continue;
                seen.add(key);
                const root=getRoot(a),rootText=clean(root.innerText||'');
                const lines=rootText.split(/\\n+/).map(x=>clean(x)).filter(Boolean).slice(0,12);
                let industry=null,location=null;
                for(const ln of lines){
                  if(!industry&&/consulting|software|technology|services|financial|health|education|manufacturing|marketing|retail|media|logistics/i.test(ln)){industry=ln;continue;}
                  if(!location&&/,/.test(ln)&&!/followers?/i.test(ln)&&ln.length<=80){location=ln;}
                }
                results.push({title,primary_url:href,industry,location});
              }
              return results;
            }
            """)
        except Exception:
            return []

        seen_keys = set()
        deduped = []
        for r in (raw or []):
            key = (r.get("primary_url", ""), r.get("title", ""))
            if key in seen_keys:
                continue
            seen_keys.add(key)
            deduped.append(r)
        return deduped

    # ------------------------------------------------------------------
    # Scoring
    # ------------------------------------------------------------------

    def _score(self, result: Dict[str, Any], norm_query: str) -> Tuple[int, List[str]]:
        title = self._norm(result.get("title", ""))
        href = result.get("primary_url", "")
        slug = self._extract_slug(href)
        industry = result.get("industry") or ""
        location = result.get("location") or ""

        score = 0
        reasons: List[str] = []

        if "/company/" in href.lower():
            score += 55; reasons.append("company_url")

        if title == norm_query:
            score += 35; reasons.append("exact_match")
        elif title.startswith(norm_query):
            score += 24; reasons.append("title_starts_with")
        elif norm_query in title:
            score += 16; reasons.append("query_in_title")
        else:
            ov = self._token_overlap(title, norm_query)
            if ov >= 0.8:
                score += 18; reasons.append("high_token_overlap")
            elif ov >= 0.5:
                score += 10; reasons.append("medium_token_overlap")

        if slug:
            ns = self._norm_slug(slug)
            if ns == norm_query:
                score += 16; reasons.append("exact_slug")
            elif norm_query in ns or ns in norm_query:
                score += 8; reasons.append("partial_slug")
            else:
                ov = self._token_overlap(ns, norm_query)
                if ov >= 0.5:
                    score += 6; reasons.append("slug_overlap")

        if industry:
            score += 4; reasons.append("industry_present")
        if location:
            score += 4; reasons.append("location_present")

        low_title = title.lower()
        if "salesforce - " in low_title or " page by " in low_title:
            score -= 25; reasons.append("subpage_penalty")

        return min(100,score), reasons

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _settle(self) -> None:
        try:
            self.page.wait_for_load_state("networkidle", timeout=3000)
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

    def _norm(self, name: str) -> str:
        x = (name or "").strip().lower()
        x = x.replace("&", " and ")
        x = re.sub(r"[^\w\s]", " ", x)
        x = re.sub(r"\b(private limited|pvt ltd|pvt\.? ltd\.?|limited|ltd|llp|inc|corp|co)\b", " ", x)
        return re.sub(r"\s+", " ", x).strip()

    def _norm_slug(self, slug: str) -> str:
        x = (slug or "").lower().strip("/").replace("-", " ")
        x = re.sub(r"[^\w\s]", " ", x)
        return re.sub(r"\s+", " ", x).strip()

    def _extract_slug(self, url: str) -> str:
        try:
            parts = urlparse(url).path.strip("/").split("/")
            if len(parts) >= 2 and parts[0] in {"company", "showcase", "school"}:
                return parts[1]
        except Exception:
            pass
        return ""

    def _classify_href(self, href: str) -> str:
        x = (href or "").lower()
        if "/company/" in x:
            return "company"
        if "/showcase/" in x:
            return "showcase"
        if "/school/" in x:
            return "school"
        return "other"

    def _canonicalize(self, url: str) -> str:
        p = urlparse(url)
        path = p.path.rstrip("/") + "/"
        return urlunparse((p.scheme, p.netloc, path, "", "", ""))

    def _token_overlap(self, a: str, b: str) -> float:
        ta, tb = set(a.split()), set(b.split())
        if not ta or not tb:
            return 0.0
        return len(ta & tb) / max(1, min(len(ta), len(tb)))
