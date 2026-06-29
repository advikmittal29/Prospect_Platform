from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Tuple

from candidate_hunt.schemas import (
    CandidateAssessment,
    CandidateProfileIntelligence,
    CandidateSearchCard,
    JobContext,
)
from config import LLMConfig
from intelligence.dossier_generator import LLMClient
from utils.logging import build_logger
from utils.prompt_loader import render_prompt

logger = build_logger("prospect.candidate_hunt.scoring")


class CandidateAssessor:
    """Evidence-based job-seeking and JD relevance assessment."""

    DIRECT_POSITIVE = [
        r"\bopen to work\b",
        r"\bactively seeking\b",
        r"\blooking for opportunities\b",
        r"\bavailable immediately\b",
    ]
    SEMI_POSITIVE = [
        r"\bopen to opportunities\b",
        r"\bavailable for\b",
        r"\bfreelance\b",
        r"\bconsulting\b",
        r"\bnew challenge\b",
        r"\btransition\b",
    ]
    BEHAVIORAL_POSITIVE = [
        r"\blayoff\b",
        r"\breferral\b",
        r"\bhiring\b",
        r"\bresume\b",
        r"\bjob search\b",
        r"\bopportunity\b",
    ]
    NEGATIVE = [
        r"\bnot looking\b",
        r"\bnot open to opportunities\b",
        r"\bjust joined\b",
        r"\bjoined as\b",
        r"\bno recruiter\b",
    ]

    def __init__(self, llm_cfg: LLMConfig, *, prompt_context: Optional[Dict[str, Any]] = None) -> None:
        self._prompt_context = dict(prompt_context or {})
        self._llm: Optional[LLMClient] = None
        if llm_cfg.api_key:
            try:
                self._llm = LLMClient(llm_cfg)
            except Exception as exc:
                logger.warning("Candidate scoring LLM disabled, using deterministic mode: %s", exc)

    def assess(
        self,
        job: JobContext,
        card: CandidateSearchCard,
        profile: CandidateProfileIntelligence,
    ) -> CandidateAssessment:
        joined_text = self._build_search_text(card, profile)

        status, seek_score, confidence, top_evidence, negative_evidence, ambiguity = self._assess_job_seeking(
            joined_text, card, profile
        )

        dims, missing = self._assess_jd_relevance(job, card, profile)
        relevance_score = self._weighted_relevance(dims)

        summary = self._build_summary(status, seek_score, relevance_score, dims, top_evidence, missing)
        llm_payload = self._llm_refine(
            job=job,
            card=card,
            profile=profile,
            baseline={
                "job_seeking_status": status,
                "job_seeking_score": seek_score,
                "confidence_score": confidence,
                "top_evidence": top_evidence,
                "negative_evidence": negative_evidence,
                "ambiguity_notes": ambiguity,
                "jd_relevance_score": relevance_score,
                "jd_dimension_scores": dims,
                "missing_critical_requirements": missing,
                "llm_summary_text": summary,
            },
        )

        if llm_payload:
            status = self._safe_status(llm_payload.get("job_seeking_status"), status)
            seek_score = self._clamp_int(llm_payload.get("job_seeking_score"), seek_score)
            confidence = self._clamp_int(llm_payload.get("confidence_score"), confidence)
            relevance_score = self._clamp_int(llm_payload.get("jd_relevance_score"), relevance_score)

            if isinstance(llm_payload.get("jd_dimension_scores"), dict):
                cleaned_dims: Dict[str, int] = {}
                for key, val in llm_payload["jd_dimension_scores"].items():
                    cleaned_dims[str(key)] = self._clamp_int(val, dims.get(str(key), 0))
                dims.update(cleaned_dims)

            top_evidence = self._norm_list(llm_payload.get("top_evidence"), top_evidence)
            negative_evidence = self._norm_list(llm_payload.get("negative_evidence"), negative_evidence)
            ambiguity = self._norm_list(llm_payload.get("ambiguity_notes"), ambiguity)
            missing = self._norm_list(llm_payload.get("missing_critical_requirements"), missing)
            if isinstance(llm_payload.get("llm_summary_text"), str) and llm_payload["llm_summary_text"].strip():
                summary = llm_payload["llm_summary_text"].strip()[:3000]

        return CandidateAssessment(
            job_seeking_status=status,
            job_seeking_score=seek_score,
            confidence_score=confidence,
            top_evidence=top_evidence,
            negative_evidence=negative_evidence,
            ambiguity_notes=ambiguity,
            jd_relevance_score=relevance_score,
            jd_dimension_scores=dims,
            missing_critical_requirements=missing,
            llm_summary_text=summary,
            llm_payload=llm_payload,
        )

    def _assess_job_seeking(
        self,
        text: str,
        card: CandidateSearchCard,
        profile: CandidateProfileIntelligence,
    ) -> Tuple[str, int, int, List[str], List[str], List[str]]:
        score = 0
        positives: List[str] = []
        negatives: List[str] = []
        ambiguity: List[str] = []

        if card.is_open_to_work:
            score += 45
            positives.append("Open-to-work signal on LinkedIn search card")

        for pattern in self.DIRECT_POSITIVE:
            if re.search(pattern, text, re.I):
                score += 20
                positives.append(f"Direct job-seeking phrase matched: {pattern}")

        for pattern in self.SEMI_POSITIVE:
            if re.search(pattern, text, re.I):
                score += 10
                positives.append(f"Semi-direct openness phrase matched: {pattern}")

        for pattern in self.BEHAVIORAL_POSITIVE:
            if re.search(pattern, text, re.I):
                score += 6
                positives.append(f"Behavioral signal matched: {pattern}")

        for pattern in self.NEGATIVE:
            if re.search(pattern, text, re.I):
                score -= 18
                negatives.append(f"Negative signal matched: {pattern}")

        if profile.activity:
            score += min(10, len(profile.activity) * 2)
            positives.append("Recent activity available for review")
        else:
            ambiguity.append("No recent activity captured")

        score = max(0, min(100, score))

        if score >= 70:
            status = "actively_looking"
        elif score >= 45:
            status = "open_or_passive"
        elif score >= 25:
            status = "unclear"
        else:
            status = "unlikely_looking"

        confidence = min(100, 40 + len(positives) * 8 + (15 if card.is_open_to_work else 0) - len(ambiguity) * 6)
        confidence = max(20, confidence)

        return status, score, confidence, positives[:12], negatives[:12], ambiguity[:8]

    def _assess_jd_relevance(
        self,
        job: JobContext,
        card: CandidateSearchCard,
        profile: CandidateProfileIntelligence,
    ) -> Tuple[Dict[str, int], List[str]]:
        candidate_title = " ".join(
            filter(None, [profile.current_title, profile.profile_headline, card.headline])
        )
        job_title = " ".join(filter(None, [job.title, job.role, job.role_category]))

        title_similarity = int(self._token_similarity(job_title, candidate_title) * 100)

        required_skills = [s.lower() for s in (job.skills or [])[:8]]
        preferred_skills = [s.lower() for s in (job.skills or [])[8:16]]
        candidate_skill_text = " ".join(profile.skills + [x.get("snippet", "") for x in profile.experiences]).lower()

        required_skill_match = self._list_match_score(required_skills, candidate_skill_text)
        preferred_skill_match = self._list_match_score(preferred_skills, candidate_skill_text) if preferred_skills else required_skill_match

        seniority_fit = self._seniority_fit(job_title, candidate_title)
        experience_fit = self._experience_fit(job.experience_text, profile.experiences)
        domain_fit = self._domain_fit(job.industry, candidate_title + " " + (profile.profile_about_text or ""))
        location_fit = self._location_fit(job.location_text, card.location_text, profile.profile_location)
        evidence_completeness = self._evidence_completeness(profile)

        dims = {
            "title_similarity": title_similarity,
            "required_skill_match": required_skill_match,
            "preferred_skill_match": preferred_skill_match,
            "experience_fit": experience_fit,
            "seniority_fit": seniority_fit,
            "domain_fit": domain_fit,
            "location_fit": location_fit,
            "evidence_completeness": evidence_completeness,
        }

        missing: List[str] = []
        if title_similarity < 35:
            missing.append("Low title similarity with target job")
        if required_skill_match < 40:
            missing.append("Required skills match is weak")
        if seniority_fit < 40:
            missing.append("Seniority mismatch")
        if location_fit < 30:
            missing.append("Location mismatch or unknown")

        return dims, missing

    def _weighted_relevance(self, dims: Dict[str, int]) -> int:
        weights = {
            "title_similarity": 0.2,
            "required_skill_match": 0.22,
            "preferred_skill_match": 0.1,
            "experience_fit": 0.14,
            "seniority_fit": 0.1,
            "domain_fit": 0.1,
            "location_fit": 0.07,
            "evidence_completeness": 0.07,
        }
        score = 0.0
        for key, weight in weights.items():
            score += dims.get(key, 0) * weight
        return max(0, min(100, int(round(score))))

    def _llm_refine(
        self,
        *,
        job: JobContext,
        card: CandidateSearchCard,
        profile: CandidateProfileIntelligence,
        baseline: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        if self._llm is None:
            return None

        system = render_prompt(
            "candidate_scoring_refine_system",
            agent_context=self._prompt_context,
        )
        user = json.dumps(
            {
                "job": {
                    "job_id": job.job_id,
                    "title": job.title,
                    "company_name": job.company_name,
                    "location_text": job.location_text,
                    "experience_text": job.experience_text,
                    "industry": job.industry,
                    "skills": job.skills[:20],
                    "job_description_excerpt": (job.job_description_text or "")[:3000],
                },
                "candidate_card": {
                    "full_name": card.full_name,
                    "headline": card.headline,
                    "location_text": card.location_text,
                    "current_summary_text": card.current_summary_text,
                    "connection_degree": card.connection_degree,
                    "is_open_to_work": card.is_open_to_work,
                },
                "candidate_profile": {
                    "profile_name": profile.profile_name,
                    "profile_headline": profile.profile_headline,
                    "profile_location": profile.profile_location,
                    "profile_about_text": (profile.profile_about_text or "")[:2000],
                    "current_title": profile.current_title,
                    "current_company": profile.current_company,
                    "skills": profile.skills[:30],
                    "experiences": profile.experiences[:8],
                    "education": profile.education[:5],
                    "activity": profile.activity[:5],
                    "contact_points": profile.contact_points[:10],
                },
                "baseline": baseline,
            },
            ensure_ascii=False,
        )

        try:
            raw = self._llm.chat(system, user)
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else None
        except Exception as exc:
            logger.warning("Candidate scoring LLM refinement failed: %s", exc)
            return None

    def _build_summary(
        self,
        status: str,
        seeking_score: int,
        relevance_score: int,
        dims: Dict[str, int],
        evidence: List[str],
        missing: List[str],
    ) -> str:
        lines = [
            f"Job-seeking status: {status} ({seeking_score}/100).",
            f"JD relevance score: {relevance_score}/100.",
            "Dimension highlights: "
            + ", ".join(f"{k}={v}" for k, v in sorted(dims.items(), key=lambda item: item[1], reverse=True)[:4]),
        ]
        if evidence:
            lines.append("Top evidence: " + "; ".join(evidence[:3]))
        if missing:
            lines.append("Critical gaps: " + "; ".join(missing[:3]))
        return " ".join(lines)[:2500]

    def _build_search_text(self, card: CandidateSearchCard, profile: CandidateProfileIntelligence) -> str:
        parts = [
            card.headline,
            card.current_summary_text,
            profile.profile_headline,
            profile.profile_about_text,
            " ".join(x.get("snippet", "") for x in profile.activity),
            " ".join(profile.skills),
        ]
        return " ".join(p for p in parts if p).lower()

    def _token_similarity(self, left: str, right: str) -> float:
        l = self._tokenize(left)
        r = self._tokenize(right)
        if not l or not r:
            return 0.0
        inter = len(l & r)
        return inter / max(1, min(len(l), len(r)))

    def _list_match_score(self, required: List[str], candidate_text: str) -> int:
        if not required:
            return 50
        hits = 0
        for skill in required:
            if not skill:
                continue
            if re.search(rf"\b{re.escape(skill)}\b", candidate_text, re.I):
                hits += 1
        return int((hits / max(1, len(required))) * 100)

    def _seniority_fit(self, job_title: str, candidate_title: str) -> int:
        job = job_title.lower()
        cand = candidate_title.lower()
        job_level = self._infer_level(job)
        cand_level = self._infer_level(cand)
        if job_level == cand_level:
            return 85
        if abs(job_level - cand_level) == 1:
            return 60
        return 30

    def _infer_level(self, text: str) -> int:
        if re.search(r"\b(intern|trainee|junior|associate)\b", text, re.I):
            return 1
        if re.search(r"\b(senior|lead|principal)\b", text, re.I):
            return 3
        if re.search(r"\b(manager|head|director|vp|chief|founder|cto|ceo)\b", text, re.I):
            return 4
        return 2

    def _experience_fit(self, experience_text: Optional[str], experiences: List[Dict[str, Any]]) -> int:
        if not experience_text:
            return 50
        years_needed = self._extract_years(experience_text)
        if years_needed is None:
            return 50

        years_seen = 0
        for row in experiences:
            snippet = str(row.get("duration") or row.get("snippet") or "")
            extracted = self._extract_years(snippet)
            if extracted is not None:
                years_seen = max(years_seen, extracted)

        if years_seen == 0:
            return 45
        if years_seen >= years_needed:
            return 85
        if years_seen >= max(1, years_needed - 1):
            return 65
        return 35

    def _domain_fit(self, industry: Optional[str], text: str) -> int:
        if not industry:
            return 50
        tokens = self._tokenize(industry)
        if not tokens:
            return 50
        candidate = self._tokenize(text)
        overlap = len(tokens & candidate)
        if overlap == 0:
            return 35
        return min(90, 40 + overlap * 15)

    def _location_fit(self, job_loc: Optional[str], card_loc: Optional[str], profile_loc: Optional[str]) -> int:
        if not job_loc:
            return 50
        job_tokens = self._tokenize(job_loc)
        candidate_tokens = self._tokenize(" ".join(x for x in [card_loc, profile_loc] if x))
        if not job_tokens or not candidate_tokens:
            return 40
        overlap = len(job_tokens & candidate_tokens)
        if overlap == 0:
            return 25
        return min(90, 45 + overlap * 18)

    def _evidence_completeness(self, profile: CandidateProfileIntelligence) -> int:
        checks = [
            bool(profile.profile_about_text),
            bool(profile.current_title),
            bool(profile.experiences),
            bool(profile.education),
            bool(profile.skills),
            bool(profile.activity),
            bool(profile.contact_points),
        ]
        ratio = sum(1 for c in checks if c) / len(checks)
        return int(ratio * 100)

    def _extract_years(self, text: str) -> Optional[int]:
        if not text:
            return None
        text = text.lower()
        match = re.search(r"(\d+)\s*(\+)?\s*(years?|yrs?|yr)", text)
        if match:
            return int(match.group(1))
        match = re.search(r"(\d+)\s*(months?|mos?)", text)
        if match:
            return max(1, int(round(int(match.group(1)) / 12)))
        return None

    def _tokenize(self, text: str) -> set[str]:
        words = re.findall(r"[a-z0-9]+", (text or "").lower())
        stop = {
            "and", "or", "the", "a", "an", "of", "to", "in", "for", "with", "at", "on", "by", "from"
        }
        return {w for w in words if len(w) > 2 and w not in stop}

    def _clamp_int(self, value: Any, fallback: int) -> int:
        try:
            return max(0, min(100, int(value)))
        except Exception:
            return fallback

    def _safe_status(self, value: Any, fallback: str) -> str:
        allowed = {"actively_looking", "open_or_passive", "unclear", "unlikely_looking"}
        v = str(value or "").strip().lower()
        return v if v in allowed else fallback

    def _norm_list(self, value: Any, fallback: List[str]) -> List[str]:
        if not isinstance(value, list):
            return fallback
        out: List[str] = []
        seen = set()
        for item in value:
            text = re.sub(r"\s+", " ", str(item)).strip()
            if not text:
                continue
            key = text.lower()
            if key in seen:
                continue
            seen.add(key)
            out.append(text)
        return out[:20] if out else fallback
