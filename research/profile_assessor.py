"""
LinkedIn profile assessor.
Visits an individual profile page and extracts detailed signals used to build
high-quality prospect intelligence for outreach.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import urlparse, urlunparse

from playwright.sync_api import Page

from config import LLMConfig
from research.linkedin_browser import detect_linkedin_auth_screen
from utils.logging import build_logger
from utils.prompt_loader import render_prompt

logger = build_logger("prospect.research.profile_assessor")


@dataclass
class ProfileAssessment:
    profile_url: str

    # Identity
    name: Optional[str]
    pronouns: Optional[str]
    connection_degree: Optional[str]
    headline: Optional[str]
    location: Optional[str]

    # Role (from top-card)
    current_company_topcard: Optional[str]
    current_title_topcard: Optional[str]

    # Role (from experience section more reliable)
    current_company_experience: Optional[str]
    current_title_experience: Optional[str]
    tenure_hint: Optional[str]

    # Content signals
    about_text: Optional[str]
    profile_summary_text: Optional[str]
    experiences: List[Dict[str, str]] = field(default_factory=list)
    recent_posts: List[str] = field(default_factory=list)
    llm_assessment: Optional[Dict[str, Any]] = None

    # Interaction availability
    contact_info_available: bool = False
    message_available: bool = False
    connect_available: bool = False

    # Scores
    company_match_confidence: int = 0   # 0-100
    role_bucket: str = "unknown"
    outreach_feasibility_score: int = 0  # 0-100
    contact_relevance_score: int = 0     # 0-100
    contact_relevance_bucket: str = "skip"    # prime | strong | moderate | weak | skip

    reasons: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


class LinkedInProfileAssessor:
    """
    Given a profile URL and target company context, navigates to the profile
    and produces a ProfileAssessment with scored signals.
    """

    ROLE_BUCKETS = {
        "ta_leader", "hr_leader", "recruitment", "hr", "hiring_manager",
        "exec_tech", "exec_business", "tech_leader", "founder", "other", "unknown",
    }
    RELEVANCE_BUCKETS = {"prime", "strong", "moderate", "weak", "skip"}

    POSITIVE_ROLE_PATTERNS: List[Tuple[str, str, int]] = [
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
        (r"\bdirector of hr\b", "hr_leader", 35),
        (r"\bpeople operations\b", "hr", 28),
        (r"\bchief human resources\b", "hr_leader", 40),
    ]

    NEGATIVE_ROLE_PATTERNS: List[Tuple[str, int]] = [
        (r"\bintern\b", -30),
        (r"\btrainee\b", -25),
        (r"\bstudent\b", -25),
        (r"\bsoftware engineer\b", -15),
        (r"\bdeveloper\b", -15),
        (r"\btester\b", -15),
        (r"\banalyst\b", -12),
        (r"\bassociate\b", -10),
    ]

    HIRING_SIGNAL_PATTERNS: List[Tuple[str, int]] = [
        (r"\bhiring\b", 12),
        (r"\brecruit(ing|ment)?\b", 12),
        (r"\btalent acquisition\b", 14),
        (r"\bteam building\b", 8),
        (r"\bscaling teams?\b", 12),
        (r"\bbuild(ing)? teams?\b", 10),
        (r"\binterview(ing)?\b", 8),
        (r"\bstaffing\b", 10),
        (r"\bopen to\b", 6),
        (r"\bwe['']?re hiring\b", 15),
    ]

    def __init__(
        self,
        page: Page,
        auth_handler: Optional[Callable[[], bool]] = None,
        llm_config: Optional[LLMConfig] = None,
        prompt_context: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.page = page
        self._auth_handler = auth_handler
        self._prompt_context = dict(prompt_context or {})
        self._llm = None
        self._llm_model = llm_config.model if llm_config else None

        if llm_config and llm_config.api_key:
            try:
                from intelligence.dossier_generator import LLMClient
                self._llm = LLMClient(llm_config)
            except Exception as exc:
                logger.warning("LLM assessor disabled, client init failed: %s", exc)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def assess(
        self,
        profile_url: str,
        target_company_name: str,
        target_company_url: Optional[str] = None,
    ) -> ProfileAssessment:
        canonical_url = self._canon_profile(profile_url)
        logger.debug("Assessing profile: %s", canonical_url)

        try:
            self._goto_with_auth(canonical_url, timeout=15000)
        except Exception as exc:
            logger.warning("Profile nav failed %s: %s", canonical_url, exc)
            return self._empty_assessment(canonical_url, f"nav_error:{exc}")

        try:
            raw = self._extract_profile_data()
            raw["recent_posts"] = self._extract_recent_posts(canonical_url)
        except Exception as exc:
            logger.warning("Profile extraction failed %s: %s", canonical_url, exc)
            return self._empty_assessment(canonical_url, f"extract_error:{exc}")

        # Heuristic baseline.
        company_match, company_reasons = self._score_company_match(
            raw, target_company_name, target_company_url
        )
        role_score, role_bucket = self._score_role(raw)
        hiring_score = self._score_hiring_signals(raw)
        feasibility = self._score_feasibility(raw)
        relevance_score, relevance_bucket = self._compute_relevance(
            company_match, role_score, hiring_score, feasibility
        )

        reasons = company_reasons.copy()
        warnings: List[str] = []
        if raw.get("is_open_to_work"):
            reasons.append("open_to_work_signal")
        if company_match < 50:
            warnings.append("low_company_match_confidence")
        if not raw.get("connect_available") and not raw.get("message_available"):
            warnings.append("no_direct_contact_path")

        profile_summary = self._build_fallback_summary(raw, relevance_bucket, relevance_score)
        llm_payload = self._llm_assess(
            raw=raw,
            target_company_name=target_company_name,
            target_company_url=target_company_url,
            baseline={
                "company_match_confidence": company_match,
                "role_bucket": role_bucket,
                "outreach_feasibility_score": feasibility,
                "contact_relevance_score": relevance_score,
                "contact_relevance_bucket": relevance_bucket,
                "reasons": reasons,
                "warnings": warnings,
                "profile_summary_text": profile_summary,
            },
        )
        if llm_payload:
            company_match = self._clamp_int(llm_payload.get("company_match_confidence"), company_match)
            feasibility = self._clamp_int(llm_payload.get("outreach_feasibility_score"), feasibility)
            relevance_score = self._clamp_int(llm_payload.get("contact_relevance_score"), relevance_score)

            role_bucket = self._safe_role_bucket(llm_payload.get("role_bucket"), role_bucket)
            relevance_bucket = self._safe_relevance_bucket(
                llm_payload.get("contact_relevance_bucket"), relevance_bucket
            )
            if llm_payload.get("profile_summary_text"):
                profile_summary = str(llm_payload["profile_summary_text"]).strip()[:4000]
            if isinstance(llm_payload.get("reasons"), list):
                reasons = [str(x).strip() for x in llm_payload["reasons"] if str(x).strip()][:20]
            if isinstance(llm_payload.get("warnings"), list):
                warnings = [str(x).strip() for x in llm_payload["warnings"] if str(x).strip()][:20]

        return ProfileAssessment(
            profile_url=canonical_url,
            name=raw.get("name"),
            pronouns=raw.get("pronouns"),
            connection_degree=raw.get("connection_degree"),
            headline=raw.get("headline"),
            location=raw.get("location"),
            current_company_topcard=raw.get("current_company_topcard"),
            current_title_topcard=raw.get("current_title_topcard"),
            current_company_experience=raw.get("current_company_experience"),
            current_title_experience=raw.get("current_title_experience"),
            tenure_hint=raw.get("tenure_hint"),
            about_text=raw.get("about_text"),
            profile_summary_text=profile_summary,
            experiences=raw.get("experiences") or [],
            recent_posts=raw.get("recent_posts") or [],
            llm_assessment=llm_payload,
            contact_info_available=bool(raw.get("contact_info_available")),
            message_available=bool(raw.get("message_available")),
            connect_available=bool(raw.get("connect_available")),
            company_match_confidence=company_match,
            role_bucket=role_bucket,
            outreach_feasibility_score=feasibility,
            contact_relevance_score=relevance_score,
            contact_relevance_bucket=relevance_bucket,
            reasons=reasons,
            warnings=warnings,
        )

    # ------------------------------------------------------------------
    # Data extraction (DOM -> dict)
    # ------------------------------------------------------------------

    def _extract_profile_data(self) -> dict:
        return self.page.evaluate(
            """
            () => {
              function clean(s){return(s||'').replace(/\\s+/g,' ').trim();}
              function has(sel){return document.querySelector(sel)!==null;}

              const nameEl=document.querySelector('.pv-text-details__left-panel h1,.text-heading-xlarge');
              const pronounsEl=document.querySelector('.pv-text-details__left-panel .text-body-small:not(.break-words)');
              const headlineEl=document.querySelector('.pv-text-details__left-panel .text-body-medium.break-words,.pv-text-details__left-panel [data-field="headline"]');
              const locationEl=document.querySelector('.pv-text-details__left-panel .text-body-small.inline.t-black--light');
              const degreeEl=document.querySelector('.pv-text-details__left-panel .dist-value,.distance-badge');

              const summaryItems=[...document.querySelectorAll('.pv-text-details__right-panel-item-text,.pv-text-details__left-panel .mt1 span')];
              let currentTitleTopcard=null,currentCompanyTopcard=null;
              if(summaryItems.length>0){currentTitleTopcard=clean(summaryItems[0].innerText);}
              if(summaryItems.length>1){currentCompanyTopcard=clean(summaryItems[1].innerText);}

              const expSection=document.querySelector('#experience~div,section[id*="experience"],#experience');
              let currentTitleExp=null,currentCompanyExp=null,tenureHint=null;
              const experiences=[];
              if(expSection){
                const items=[...expSection.querySelectorAll('li.artdeco-list__item,[data-field="experience_group"],li')]
                  .filter(el => clean(el.innerText||'').length>0)
                  .slice(0,10);
                for(const item of items){
                  const lines=(item.innerText||'').split(/\\n+/).map(clean).filter(Boolean);
                  if(!lines.length){continue;}
                  const title=lines[0]||null;
                  const company=lines[1]||null;
                  const duration=lines.find(x=>/present|yr|year|month|mo|\\d{4}/i.test(x))||null;
                  const snippet=clean(lines.slice(0,5).join(' | '));
                  if(snippet.length<8){continue;}
                  experiences.push({
                    title:title||'',
                    company:company||'',
                    duration:duration||'',
                    snippet:snippet
                  });
                }
                if(experiences.length>0){
                  currentTitleExp=experiences[0].title||null;
                  currentCompanyExp=experiences[0].company||null;
                  tenureHint=experiences[0].duration||null;
                }
              }

              const aboutSection=document.querySelector('#about~div,section[id*="about"] .inline-show-more-text,section[id*="about"] .pv-about__summary-text');
              const aboutText=aboutSection?clean(aboutSection.innerText):null;

              const buttons=[...document.querySelectorAll('button,a[data-control-name]')];
              const buttonTexts=buttons.map(b=>(b.innerText||b.getAttribute('aria-label')||'').toLowerCase().trim());
              const messageAvail=buttonTexts.some(t=>t.includes('message'));
              const connectAvail=buttonTexts.some(t=>t.includes('connect'));
              const contactInfoAvail=has('a[href*="contact-info"],#contact-info,section[id*="contact"]');

              const openToWork=has('.pv-open-to-work-typeahead-text,.open-to-work-status-indicator,[data-test-id*="open-to-work"]');
              const hiringBadge=has('[data-test-id*="is-hiring"],[aria-label*="hiring"],[data-control-name*="hiring"]');

              return{
                name:nameEl?clean(nameEl.innerText):null,
                pronouns:pronounsEl?clean(pronounsEl.innerText):null,
                connection_degree:degreeEl?clean(degreeEl.innerText):null,
                headline:headlineEl?clean(headlineEl.innerText):null,
                location:locationEl?clean(locationEl.innerText):null,
                current_title_topcard:currentTitleTopcard,
                current_company_topcard:currentCompanyTopcard,
                current_title_experience:currentTitleExp,
                current_company_experience:currentCompanyExp,
                tenure_hint:tenureHint,
                experiences:experiences,
                about_text:aboutText,
                message_available:messageAvail,
                connect_available:connectAvail,
                contact_info_available:contactInfoAvail,
                is_open_to_work:openToWork,
                is_hiring:hiringBadge
              };
            }
            """
        )

    def _extract_recent_posts(self, canonical_profile_url: str) -> List[str]:
        """
        Navigate to the profile's recent activity page, extract structured post
        objects (anchored to feed-shared-update-v2), filter out reposts/reshares
        and year-old content, then format for LLM consumption.

        Returns a list of formatted post strings (up to 5).
        """
        activity_url = canonical_profile_url.rstrip("/") + "/recent-activity/all/"
        try:
            self._goto_with_auth(activity_url, timeout=15000)
        except Exception as exc:
            logger.debug("Recent activity not available for %s: %s", canonical_profile_url, exc)
            return []

        try:
            raw_posts = self.page.evaluate(
                """
                () => {
                  function clean(s){
                    return (s || '').replace(/\\s+/g,' ').trim();
                  }

                  function getTime(root){
                    const el = root.querySelector(
                      '.update-components-actor__sub-description span[aria-hidden="true"]'
                    );
                    if (!el) return null;
                    return clean(el.innerText).split('•')[0].trim();
                  }

                  function getText(root){
                    const el = root.querySelector(
                      '.feed-shared-update-v2__description .break-words'
                    );
                    return el ? clean(el.innerText) : null;
                  }

                  function getAuthor(root){
                    const el = root.querySelector(
                      '.update-components-actor__title span[aria-hidden="true"]'
                    );
                    return el ? clean(el.innerText) : null;
                  }

                  function isRepost(root){
                    return !!root.querySelector('.update-components-header');
                  }

                  function isReshare(root){
                    return !!root.querySelector('.update-components-mini-update-v2');
                  }

                  const roots = Array.from(
                    document.querySelectorAll('div.feed-shared-update-v2')
                  );

                  const results = [];

                  for (const root of roots) {
                    const text = getText(root);
                    if (!text || text.length < 30) continue;

                    results.push({
                      urn: root.getAttribute('data-urn'),
                      text,
                      author: getAuthor(root),
                      posted_ago: getTime(root),
                      is_repost: isRepost(root),
                      is_reshare: isReshare(root),
                    });

                    if (results.length >= 5) break;
                  }

                  return results;
                }
                """
            )
        except Exception:
            return []

        if not isinstance(raw_posts, list):
            return []

        # Filter: keep only original posts that are not year-old
        clean_posts = [
            p for p in raw_posts
            if not p.get("is_repost")
            and not p.get("is_reshare")
            and self._is_recent_post(p)
        ]

        if not clean_posts:
            logger.debug("No recent original posts found for %s", canonical_profile_url)
            return []

        # Format for LLM with explicit labels
        return self._format_posts_for_llm(clean_posts)

    # ------------------------------------------------------------------
    # Post filtering helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _is_recent_post(post: Dict[str, Any]) -> bool:
        """Return True if the post is not year-old (months/weeks/days are OK)."""
        posted = (post.get("posted_ago") or "").lower().strip()
        if not posted:
            return False
        # Reject anything that looks like 1yr, 2yr, etc.
        if "yr" in posted:
            return False
        return True

    @staticmethod
    def _format_posts_for_llm(posts: List[Dict[str, Any]]) -> List[str]:
        """
        Produce structured, LLM-safe text blocks for each post.
        Each block carries explicit Time, Recency, and Type labels so the
        model never has to guess recency from raw text.
        """
        formatted: List[str] = []

        for i, p in enumerate(posts, 1):
            posted = p.get("posted_ago") or "unknown"
            recency = "OLD" if "yr" in posted.lower() else "RECENT"
            post_type = (
                "repost" if p.get("is_repost")
                else "reshare" if p.get("is_reshare")
                else "original"
            )
            text = (p.get("text") or "").strip()[:700]

            block = (
                f"POST {i}\n"
                f"Time: {posted}\n"
                f"Recency: {recency}\n"
                f"Type: {post_type}\n"
                f"\nText:\n{text}"
            )
            formatted.append(block)

        return formatted

    # ------------------------------------------------------------------
    # LLM enrichment
    # ------------------------------------------------------------------

    def _llm_assess(
        self,
        *,
        raw: dict,
        target_company_name: str,
        target_company_url: Optional[str],
        baseline: dict,
    ) -> Optional[Dict[str, Any]]:
        if self._llm is None:
            return None

        system = render_prompt(
            "profile_assessment_system",
            agent_context=self._prompt_context,
        )
        user = json.dumps(
            {
                "target_company_name": target_company_name,
                "target_company_url": target_company_url,
                "profile_raw": {
                    "name": raw.get("name"),
                    "headline": raw.get("headline"),
                    "location": raw.get("location"),
                    "connection_degree": raw.get("connection_degree"),
                    "current_title_topcard": raw.get("current_title_topcard"),
                    "current_company_topcard": raw.get("current_company_topcard"),
                    "current_title_experience": raw.get("current_title_experience"),
                    "current_company_experience": raw.get("current_company_experience"),
                    "tenure_hint": raw.get("tenure_hint"),
                    "about_text": raw.get("about_text"),
                    "experiences": raw.get("experiences", [])[:8],
                    "recent_posts": raw.get("recent_posts", [])[:3],
                    "message_available": raw.get("message_available"),
                    "connect_available": raw.get("connect_available"),
                    "contact_info_available": raw.get("contact_info_available"),
                    "is_open_to_work": raw.get("is_open_to_work"),
                    "is_hiring": raw.get("is_hiring"),
                },
                "heuristic_baseline": baseline,
            },
            ensure_ascii=False,
        )

        try:
            payload = self._llm.chat(system, user)
            data = json.loads(payload)
            if not isinstance(data, dict):
                return None
            return data
        except Exception as exc:
            logger.warning("LLM profile assessment failed: %s", exc)
            return None

    def _build_fallback_summary(self, raw: dict, bucket: str, score: int) -> str:
        name = raw.get("name") or "This prospect"
        title = raw.get("current_title_experience") or raw.get("current_title_topcard") or raw.get("headline") or "unknown role"
        company = raw.get("current_company_experience") or raw.get("current_company_topcard") or "unknown company"
        about = (raw.get("about_text") or "").strip()
        posts = raw.get("recent_posts") or []
        exp = raw.get("experiences") or []

        parts = [
            f"{name} currently appears to be {title} at {company}.",
            f"Relevance bucket is {bucket} with score {score}.",
        ]
        if about:
            parts.append("About: " + re.sub(r"\s+", " ", about)[:300] + ".")
        if exp:
            top = exp[0]
            snippet = top.get("snippet") or ""
            if snippet:
                parts.append("Latest experience: " + re.sub(r"\s+", " ", snippet)[:220] + ".")
        if posts:
            # posts are formatted blocks; extract the text body for a clean summary signal
            first_post = posts[0]
            text_body = ""
            in_text = False
            for line in first_post.splitlines():
                if line.startswith("Text:"):
                    in_text = True
                    continue
                if in_text and line.strip():
                    text_body = line.strip()
                    break
            signal = text_body or re.sub(r"POST \d+.*?Text:", "", first_post, flags=re.S).strip()
            parts.append("Recent signal: " + re.sub(r"\s+", " ", signal)[:220] + ".")
        return " ".join(parts)[:900]

    # ------------------------------------------------------------------
    # Scoring helpers
    # ------------------------------------------------------------------

    def _score_company_match(
        self, raw: dict, target_company: str, target_url: Optional[str]
    ) -> Tuple[int, List[str]]:
        score = 0
        reasons: List[str] = []

        norm_target = self._norm(target_company)
        fields = [
            raw.get("current_company_topcard"),
            raw.get("current_company_experience"),
            raw.get("headline"),
        ]
        for exp in raw.get("experiences") or []:
            fields.append(exp.get("company"))
            fields.append(exp.get("snippet"))

        for field_val in fields:
            if not field_val:
                continue
            norm_val = self._norm(str(field_val))
            if norm_target and norm_target in norm_val:
                score = max(score, 90)
                reasons.append("company_name_in_profile")
                break
            overlap = self._token_overlap(norm_target, norm_val)
            if overlap >= 0.7:
                score = max(score, 75)
                reasons.append("high_company_token_overlap")
            elif overlap >= 0.4:
                score = max(score, 55)
                reasons.append("medium_company_token_overlap")

        if score == 0:
            reasons.append("no_company_match")
        return min(100, score), list(dict.fromkeys(reasons))

    def _score_role(self, raw: dict) -> Tuple[int, str]:
        texts: List[str] = [
            raw.get("headline") or "",
            raw.get("current_title_topcard") or "",
            raw.get("current_title_experience") or "",
        ]
        for exp in raw.get("experiences") or []:
            texts.append(exp.get("title") or "")
            texts.append(exp.get("snippet") or "")
        combined = " ".join(texts).lower()

        score = 0
        bucket = "other"
        for pattern, bkt, pts in self.POSITIVE_ROLE_PATTERNS:
            if re.search(pattern, combined, re.I):
                score += pts
                bucket = bkt
                break
        for pattern, penalty in self.NEGATIVE_ROLE_PATTERNS:
            if re.search(pattern, combined, re.I):
                score += penalty
        if re.search(r"\bceo\b|\bcto\b|\bfounder\b|\bhead of\b|\bvp\b|\bdirector\b|\bmanager\b", combined, re.I):
            score += 15
        return max(0, min(100, score)), bucket

    @staticmethod
    def _extract_post_text(posts: list) -> str:
        """
        Extract plain text from formatted post blocks produced by
        _format_posts_for_llm. Each block may contain header lines
        (POST N, Time, Recency, Type) followed by 'Text:' and body.
        """
        parts = []
        for block in posts:
            in_text = False
            for line in block.splitlines():
                if line.startswith("Text:"):
                    in_text = True
                    continue
                if in_text:
                    parts.append(line.strip())
        return " ".join(parts)

    def _score_hiring_signals(self, raw: dict) -> int:
        post_text = self._extract_post_text(raw.get("recent_posts") or [])
        texts = " ".join(
            filter(
                None,
                [
                    raw.get("headline"),
                    raw.get("about_text"),
                    raw.get("current_title_topcard"),
                    post_text,
                ],
            )
        ).lower()
        score = 0
        for pattern, pts in self.HIRING_SIGNAL_PATTERNS:
            if re.search(pattern, texts, re.I):
                score += pts
        if raw.get("is_hiring"):
            score += 20
        return min(60, score)

    def _score_feasibility(self, raw: dict) -> int:
        score = 30
        if raw.get("message_available"):
            score += 40
        elif raw.get("connect_available"):
            score += 20
        if raw.get("contact_info_available"):
            score += 20
        degree = (raw.get("connection_degree") or "").strip()
        if "1st" in degree:
            score += 25
        elif "2nd" in degree:
            score += 15
        elif "3rd" in degree:
            score += 5
        return min(100, score)

    def _compute_relevance(
        self, company_match: int, role_score: int, hiring_score: int, feasibility: int
    ) -> Tuple[int, str]:
        score = int(
            company_match * 0.35
            + role_score * 0.35
            + hiring_score * 0.15
            + feasibility * 0.15
        )
        if score >= 75:
            bucket = "prime"
        elif score >= 60:
            bucket = "strong"
        elif score >= 45:
            bucket = "moderate"
        elif score >= 30:
            bucket = "weak"
        else:
            bucket = "skip"
        return min(100, score), bucket

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _clamp_int(self, value: Any, fallback: int) -> int:
        try:
            return max(0, min(100, int(value)))
        except Exception:
            return fallback

    def _safe_role_bucket(self, value: Any, fallback: str) -> str:
        v = str(value or "").strip().lower()
        return v if v in self.ROLE_BUCKETS else fallback

    def _safe_relevance_bucket(self, value: Any, fallback: str) -> str:
        v = str(value or "").strip().lower()
        return v if v in self.RELEVANCE_BUCKETS else fallback

    def _settle(self) -> None:
        try:
            self.page.wait_for_load_state("networkidle", timeout=3000)
        except Exception:
            pass
        self.page.wait_for_timeout(800)

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

    def _norm(self, text: str) -> str:
        x = (text or "").strip().lower()
        x = re.sub(r"[^\w\s]", " ", x)
        return re.sub(r"\s+", " ", x).strip()

    def _token_overlap(self, a: str, b: str) -> float:
        ta, tb = set(a.split()), set(b.split())
        if not ta or not tb:
            return 0.0
        return len(ta & tb) / max(1, min(len(ta), len(tb)))

    def _canon_profile(self, url: str) -> str:
        p = urlparse(url)
        path = p.path.rstrip("/") + "/"
        return urlunparse((p.scheme or "https", p.netloc or "www.linkedin.com", path, "", "", ""))

    def _empty_assessment(self, url: str, reason: str) -> ProfileAssessment:
        return ProfileAssessment(
            profile_url=url,
            name=None,
            pronouns=None,
            connection_degree=None,
            headline=None,
            location=None,
            current_company_topcard=None,
            current_title_topcard=None,
            current_company_experience=None,
            current_title_experience=None,
            tenure_hint=None,
            about_text=None,
            profile_summary_text=None,
            experiences=[],
            recent_posts=[],
            llm_assessment=None,
            contact_info_available=False,
            message_available=False,
            connect_available=False,
            company_match_confidence=0,
            role_bucket="unknown",
            outreach_feasibility_score=0,
            contact_relevance_score=0,
            contact_relevance_bucket="skip",
            reasons=[],
            warnings=[reason],
        )
