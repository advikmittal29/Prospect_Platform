from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

from config import CandidateHuntConfig, LLMConfig
from candidate_hunt.schemas import JobContext, SearchQueryPlan
from intelligence.dossier_generator import LLMClient
from utils.logging import build_logger
from utils.prompt_loader import render_prompt

logger = build_logger("prospect.candidate_hunt.query_builder")


class CandidateQueryBuilder:
    """Derive role-keyword LinkedIn people search queries from job context."""

    TITLE_SYNONYMS: Dict[str, List[str]] = {
        "software engineer": ["software developer", "backend engineer", "full stack engineer"],
        "data scientist": ["machine learning engineer", "applied scientist", "ai engineer"],
        "data engineer": ["etl engineer", "analytics engineer", "big data engineer"],
        "devops engineer": ["site reliability engineer", "platform engineer", "cloud engineer"],
        "product manager": ["technical product manager", "program manager", "product lead"],
        "qa engineer": ["test automation engineer", "sdet", "quality engineer"],
        "ui ux designer": ["product designer", "ui designer", "ux designer"],
    }

    DEFAULT_NEGATIVE = ["intern", "student", "trainee", "fresher"]

    def __init__(
        self,
        llm_cfg: LLMConfig,
        hunt_cfg: CandidateHuntConfig,
        *,
        prompt_context: Optional[Dict[str, Any]] = None,
        keyword_overrides: Optional[List[str]] = None,
    ) -> None:
        self._llm_cfg = llm_cfg
        self._hunt_cfg = hunt_cfg
        self._prompt_context = dict(prompt_context or {})
        self._keyword_overrides = [k.strip() for k in (keyword_overrides or []) if str(k).strip()]
        self._llm: Optional[LLMClient] = None

        if llm_cfg.api_key:
            try:
                self._llm = LLMClient(llm_cfg)
            except Exception as exc:
                logger.warning("LLM query builder disabled, falling back to heuristic mode: %s", exc)

    def build(self, job: JobContext) -> SearchQueryPlan:
        heuristic = self._heuristic_plan(job)

        if self._llm is None:
            return heuristic

        try:
            llm_plan = self._build_with_llm(job)
            return self._merge_plans(heuristic, llm_plan)
        except Exception as exc:
            logger.warning("LLM query derivation failed for job %s: %s", job.job_id, exc)
            return heuristic

    def _build_with_llm(self, job: JobContext) -> SearchQueryPlan:
        system = render_prompt("candidate_query_plan_system", agent_context=self._prompt_context)

        payload = {
            "job_id": job.job_id,
            "title": job.title,
            "company_name": job.company_name,
            "location_text": job.location_text,
            "experience_text": job.experience_text,
            "industry": job.industry,
            "role": job.role,
            "role_category": job.role_category,
            "skills": job.skills[:20],
            "job_description_excerpt": (job.job_description_text or "")[:2500],
            "keyword_overrides": self._keyword_overrides[:20],
        }
        raw = self._llm.chat(system, json.dumps(payload, ensure_ascii=False))
        parsed = json.loads(raw)
        if not isinstance(parsed, dict):
            raise ValueError("LLM query plan is not a JSON object.")

        plan = SearchQueryPlan(
            role_keywords=self._norm_list(parsed.get("role_keywords")),
            alternate_titles=self._norm_list(parsed.get("alternate_titles")),
            seniority_variants=self._norm_list(parsed.get("seniority_variants")),
            skill_phrases=self._norm_list(parsed.get("skill_phrases")),
            negative_keywords=self._norm_list(parsed.get("negative_keywords")),
            llm_used=True,
        )
        plan.final_queries = self._compose_queries(job, plan)
        return plan

    def _heuristic_plan(self, job: JobContext) -> SearchQueryPlan:
        title = self._clean(job.title)
        role = self._clean(job.role)
        category = self._clean(job.role_category)

        role_keywords = [x for x in [title, role, category] if x]
        role_keywords.extend(self._keyword_overrides[:20])

        title_key = self._clean(title)
        lower_title = (title_key or "").lower()
        for base, variants in self.TITLE_SYNONYMS.items():
            if base in lower_title:
                role_keywords.extend(variants)

        if not role_keywords and job.skills:
            role_keywords.append(" ".join(job.skills[:2]).strip())

        alternate_titles = list(dict.fromkeys(self._title_variants(title_key)))
        seniority_variants = self._seniority_variants(title_key)
        skill_phrases = [s for s in job.skills[:8] if s]
        negative_keywords = list(self.DEFAULT_NEGATIVE)

        plan = SearchQueryPlan(
            role_keywords=self._norm_list(role_keywords),
            alternate_titles=self._norm_list(alternate_titles),
            seniority_variants=self._norm_list(seniority_variants),
            skill_phrases=self._norm_list(skill_phrases),
            negative_keywords=self._norm_list(negative_keywords),
            llm_used=False,
        )
        plan.final_queries = self._compose_queries(job, plan)
        return plan

    def _merge_plans(self, heuristic: SearchQueryPlan, llm_plan: SearchQueryPlan) -> SearchQueryPlan:
        merged = SearchQueryPlan(
            role_keywords=self._merge_lists(heuristic.role_keywords, llm_plan.role_keywords),
            alternate_titles=self._merge_lists(heuristic.alternate_titles, llm_plan.alternate_titles),
            seniority_variants=self._merge_lists(heuristic.seniority_variants, llm_plan.seniority_variants),
            skill_phrases=self._merge_lists(heuristic.skill_phrases, llm_plan.skill_phrases),
            negative_keywords=self._merge_lists(heuristic.negative_keywords, llm_plan.negative_keywords),
            llm_used=True,
        )
        merged.final_queries = self._compose_queries_from_fields(
            role_keywords=merged.role_keywords,
            alternate_titles=merged.alternate_titles,
            seniority_variants=merged.seniority_variants,
            skill_phrases=merged.skill_phrases,
            negative_keywords=merged.negative_keywords if self._hunt_cfg.include_negative_keywords else [],
            location=self._clean(self._hunt_cfg.search_location_fallback),
            company_name=None,
        )
        return merged

    def _compose_queries(self, job: JobContext, plan: SearchQueryPlan) -> List[str]:
        location = self._clean(job.location_text) or self._clean(self._hunt_cfg.search_location_fallback)
        negative = plan.negative_keywords if self._hunt_cfg.include_negative_keywords else []
        return self._compose_queries_from_fields(
            role_keywords=plan.role_keywords,
            alternate_titles=plan.alternate_titles,
            seniority_variants=plan.seniority_variants,
            skill_phrases=plan.skill_phrases,
            negative_keywords=negative,
            location=location,
            company_name=self._clean(job.company_name) if self._hunt_cfg.include_company_in_query else None,
        )

    def _compose_queries_from_fields(
        self,
        *,
        role_keywords: List[str],
        alternate_titles: List[str],
        seniority_variants: List[str],
        skill_phrases: List[str],
        negative_keywords: List[str],
        location: Optional[str],
        company_name: Optional[str],
    ) -> List[str]:
        queries: List[str] = []

        seed_roles = self._merge_lists(role_keywords, alternate_titles)
        if not seed_roles:
            seed_roles = ["software engineer"]

        for role in seed_roles:
            if len(queries) >= self._hunt_cfg.max_query_variants:
                break
            parts = [role]
            if seniority_variants:
                parts.append(seniority_variants[0])
            if skill_phrases:
                parts.append(skill_phrases[0])
            if location:
                parts.append(location)
            if company_name:
                parts.append(f'"{company_name}"')
            if negative_keywords:
                parts.extend(f"-{kw}" for kw in negative_keywords[:2])
            queries.append(" ".join(p for p in parts if p).strip())

        for skill in skill_phrases[:4]:
            if len(queries) >= self._hunt_cfg.max_query_variants:
                break
            base_role = seed_roles[0]
            pieces = [base_role, skill]
            if location:
                pieces.append(location)
            if company_name:
                pieces.append(f'"{company_name}"')
            queries.append(" ".join(pieces).strip())

        deduped: List[str] = []
        seen = set()
        for q in queries:
            norm = q.lower().strip()
            if not norm or norm in seen:
                continue
            seen.add(norm)
            deduped.append(q)
        return deduped[: self._hunt_cfg.max_query_variants]

    def _title_variants(self, title: Optional[str]) -> List[str]:
        if not title:
            return []
        out = [title]
        low = title.lower()
        if "senior" in low:
            out.append(re.sub(r"\bsenior\b", "lead", title, flags=re.I))
            out.append(re.sub(r"\bsenior\b", "principal", title, flags=re.I))
        if "lead" in low:
            out.append(re.sub(r"\blead\b", "senior", title, flags=re.I))
        if "manager" in low:
            out.append(re.sub(r"\bmanager\b", "head", title, flags=re.I))
        return [self._clean(x) for x in out if self._clean(x)]

    def _seniority_variants(self, title: Optional[str]) -> List[str]:
        base = (title or "").lower()
        if "intern" in base:
            return ["entry level"]
        if "junior" in base:
            return ["junior", "associate"]
        if "senior" in base or "lead" in base:
            return ["senior", "lead"]
        if "manager" in base or "director" in base or "head" in base:
            return ["manager", "director"]
        return ["senior"]

    def _merge_lists(self, first: List[str], second: List[str]) -> List[str]:
        out: List[str] = []
        seen = set()
        for value in [*(first or []), *(second or [])]:
            clean = self._clean(value)
            if not clean:
                continue
            key = clean.lower()
            if key in seen:
                continue
            seen.add(key)
            out.append(clean)
        return out

    def _norm_list(self, value: Any) -> List[str]:
        if not isinstance(value, list):
            return []
        out: List[str] = []
        seen = set()
        for item in value:
            clean = self._clean(item)
            if not clean:
                continue
            key = clean.lower()
            if key in seen:
                continue
            seen.add(key)
            out.append(clean)
        return out

    def _clean(self, value: Any) -> Optional[str]:
        if value is None:
            return None
        text = re.sub(r"\s+", " ", str(value)).strip()
        return text or None
