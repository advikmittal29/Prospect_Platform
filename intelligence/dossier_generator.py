"""
Dossier generator.

Reads assessed ProspectORM + CompanyResearchORM rows from the DB,
calls the LLM to generate structured ProspectDossier objects,
and persists the results back.
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from pydantic import ValidationError

from config import AppConfig, LLMConfig
from db import CompanyResearchORM, ProspectORM, resolve_agent_id, session_scope
from intelligence.schemas import (
    DossierBatch,
    OutreachAngle,
    ProspectDossier,
    ProspectSignal,
    RelevanceBucket,
)
from utils.logging import build_logger
from utils.prompt_loader import render_prompt

logger = build_logger("prospect.intelligence.dossier")


# ---------------------------------------------------------------------------
# LLM client wrapper (supports openai, gemini, groq)
# ---------------------------------------------------------------------------

class LLMClient:
    SUPPORTED_PROVIDERS = {"openai", "gemini", "groq"}

    def __init__(self, cfg: LLMConfig) -> None:
        self._cfg = cfg
        self._provider = (cfg.provider or "openai").strip().lower()
        self._openai_client: Optional[Any] = None
        self._groq_client: Optional[Any] = None
        self._gemini_client: Optional[Any] = None
        self._gemini_types: Optional[Any] = None

        if not cfg.api_key:
            raise ValueError("LLM_API_KEY is not set. Set it in .env or environment.")

        if self._provider not in self.SUPPORTED_PROVIDERS:
            supported = ", ".join(sorted(self.SUPPORTED_PROVIDERS))
            raise ValueError(
                f"Unsupported LLM provider: {cfg.provider!r}. Supported: {supported}."
            )

        if self._provider == "openai":
            from openai import OpenAI

            self._openai_client = OpenAI(
                api_key=cfg.api_key,
                timeout=cfg.timeout_seconds,
                max_retries=cfg.max_retries,
            )
        elif self._provider == "groq":
            try:
                from groq import Groq
            except ImportError as exc:
                raise ImportError(
                    "Groq provider selected but SDK not installed. "
                    "Install with: pip install groq"
                ) from exc
            self._groq_client = Groq(api_key=cfg.api_key)
        elif self._provider == "gemini":
            try:
                from google import genai
                from google.genai import types as genai_types
            except ImportError as exc:
                raise ImportError(
                    "Gemini provider selected but SDK not installed. "
                    "Install with: pip install google-genai"
                ) from exc
            self._gemini_client = genai.Client(api_key=cfg.api_key)
            self._gemini_types = genai_types

    def chat(self, system: str, user: str) -> str:
        """Send a chat completion request and return the text response."""
        for attempt in range(1, self._cfg.max_retries + 1):
            try:
                if self._provider == "gemini":
                    return self._chat_gemini(system, user)
                if self._provider == "groq":
                    return self._chat_groq(system, user)
                return self._chat_openai(system, user)
            except Exception as exc:
                if attempt == self._cfg.max_retries:
                    raise
                wait = self._cfg.retry_delay_seconds * (2 ** (attempt - 1))
                logger.warning(
                    "%s SDK error (attempt %d/%d): %s. Retrying in %.1fs.",
                    self._provider,
                    attempt,
                    self._cfg.max_retries,
                    exc,
                    wait,
                )
                time.sleep(wait)
        raise RuntimeError("LLM request failed after all retries.")

    def _chat_openai(self, system: str, user: str) -> str:
        if self._openai_client is None:
            raise RuntimeError("OpenAI client not initialized.")

        payload: Dict[str, Any] = {
            "model": self._cfg.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": self._cfg.temperature,
            "max_tokens": self._cfg.max_tokens,
        }
        resp = self._openai_client.chat.completions.create(
            **payload,
            response_format={"type": "json_object"},
        )
        content = resp.choices[0].message.content or ""
        return str(content).strip()

    def _chat_groq(self, system: str, user: str) -> str:
        if self._groq_client is None:
            raise RuntimeError("Groq client not initialized.")

        payload: Dict[str, Any] = {
            "model": self._cfg.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": self._cfg.temperature,
            "max_completion_tokens": self._cfg.max_tokens,
            "response_format": {"type": "json_object"},
        }
        resp = self._groq_client.chat.completions.create(**payload)
        content = resp.choices[0].message.content or ""
        return str(content).strip()

    def _chat_gemini(self, system: str, user: str) -> str:
        if self._gemini_client is None or self._gemini_types is None:
            raise RuntimeError("Gemini client not initialized.")

        config = self._gemini_types.GenerateContentConfig(
            system_instruction=system,
            temperature=self._cfg.temperature,
            max_output_tokens=self._cfg.max_tokens,
            response_mime_type="application/json",
        )
        response = self._gemini_client.models.generate_content(
            model=self._cfg.model,
            contents=user,
            config=config,
        )
        text = getattr(response, "text", None)
        if not text:
            raise RuntimeError("Gemini response did not include text output.")
        return str(text).strip()


def _build_dossier_prompt(
    prospect: ProspectORM,
    company: CompanyResearchORM,
    *,
    prompt_context: Optional[Dict[str, Any]] = None,
) -> str:
    top_experiences = " | ".join(
        (
            f"{(e.get('title') or '').strip()} @ {(e.get('company') or '').strip()} "
            f"({(e.get('duration') or '').strip()})"
        ).strip()
        for e in (prospect.experiences or [])[:5]
        if isinstance(e, dict)
    ) or "n/a"
    recent_posts = " || ".join(
        p.strip()[:260]
        for p in (prospect.recent_posts or [])[:3]
        if isinstance(p, str) and p.strip()
    ) or "n/a"
    reasons_text = ", ".join(prospect.assessment_reasons or []) or "n/a"
    warnings_text = ", ".join(prospect.assessment_warnings or []) or "n/a"

    return render_prompt(
        "dossier_user",
        agent_context=prompt_context,
        company_name=company.company_name or "unknown",
        company_linkedin=company.linkedin_url or "unknown",
        company_industry=company.industry or "unknown",
        company_size=company.employee_range or "unknown",
        company_location=company.location or "unknown",
        company_tagline=company.tagline or "n/a",
        prospect_name=prospect.name or "unknown",
        prospect_linkedin=prospect.linkedin_profile_url or "unknown",
        prospect_headline=prospect.headline or "n/a",
        prospect_location=prospect.location or "n/a",
        prospect_connection_degree=prospect.connection_degree or "unknown",
        prospect_current_title=prospect.current_title or "unknown",
        prospect_current_company=prospect.current_company or "unknown",
        prospect_tenure_hint=prospect.tenure_hint or "n/a",
        prospect_role_bucket=prospect.role_bucket or "unknown",
        profile_summary=(prospect.profile_summary_text or "n/a")[:1200],
        top_experiences=top_experiences,
        recent_posts=recent_posts,
        search_confidence=prospect.search_confidence,
        company_match_confidence=prospect.company_match_confidence,
        outreach_feasibility_score=prospect.outreach_feasibility_score,
        contact_relevance_score=prospect.contact_relevance_score,
        contact_relevance_bucket=prospect.contact_relevance_bucket or "unknown",
        message_available=prospect.message_available,
        connect_available=prospect.connect_available,
        contact_info_available=prospect.contact_info_available,
        about_text=(prospect.about_text or "n/a")[:800],
        assessment_reasons=reasons_text,
        assessment_warnings=warnings_text,
    )


# ---------------------------------------------------------------------------
# Dossier generator
# ---------------------------------------------------------------------------

class DossierGenerator:
    """
    Reads ProspectORM rows with dossier_status='pending' and contact_relevance_bucket
    in [prime, strong, moderate], generates LLM dossiers, and persists them.
    """

    ELIGIBLE_BUCKETS = {"prime", "strong", "moderate"}

    def __init__(
        self,
        config: AppConfig,
        *,
        agent_id: Optional[int] = None,
        agent_context: Optional[Dict[str, Any]] = None,
    ) -> None:
        self._cfg = config
        self._agent_id = resolve_agent_id(
            agent_id=agent_id,
            default_agent_key=config.agent_runtime.default_agent_key,
        )
        self._prompt_context = dict(agent_context or {})
        self._llm = LLMClient(config.llm)

    def run(self) -> dict:
        stats = {"processed": 0, "generated": 0, "skipped": 0, "errors": 0}

        prospects = self._load_pending_prospects()
        logger.info("Dossier generation: %d prospects to process.", len(prospects))

        if not prospects:
            return stats

        company_cache: Dict[int, CompanyResearchORM] = {}

        for prospect in prospects:
            stats["processed"] += 1
            try:
                company = self._get_company(prospect.company_research_id, company_cache)
                if company is None:
                    logger.warning("Company %d not found for prospect %d. Skipping.",
                                   prospect.company_research_id, prospect.id)
                    self._mark_skipped(prospect.id)
                    stats["skipped"] += 1
                    continue

                bucket = (prospect.contact_relevance_bucket or "").lower()
                if bucket not in self.ELIGIBLE_BUCKETS:
                    self._mark_skipped(prospect.id)
                    stats["skipped"] += 1
                    continue

                dossier = self._generate_dossier(prospect, company)
                if dossier is None:
                    self._mark_skipped(prospect.id)
                    stats["errors"] += 1
                    continue

                self._save_dossier(prospect.id, dossier)
                stats["generated"] += 1
                logger.info(
                    "  Dossier for %s: bucket=%s action=%s",
                    prospect.name or prospect.linkedin_profile_url,
                    dossier.relevance_bucket,
                    dossier.recommended_action,
                )

            except Exception as exc:
                logger.error("Error generating dossier for prospect %d: %s", prospect.id, exc, exc_info=True)
                self._mark_skipped(prospect.id)
                stats["errors"] += 1

        logger.info("Dossier generation complete. Stats: %s", stats)
        return stats

    # ------------------------------------------------------------------
    # Core generation
    # ------------------------------------------------------------------

    def _generate_dossier(
        self, prospect: ProspectORM, company: CompanyResearchORM
    ) -> Optional[ProspectDossier]:
        user_prompt = _build_dossier_prompt(
            prospect,
            company,
            prompt_context=self._prompt_context,
        )
        system_prompt = render_prompt(
            "dossier_system",
            agent_context=self._prompt_context,
        )
        try:
            raw_json = self._llm.chat(system_prompt, user_prompt)
        except Exception as exc:
            logger.error("LLM call failed for prospect %d: %s", prospect.id, exc)
            return None

        try:
            data = json.loads(raw_json)
        except json.JSONDecodeError as exc:
            logger.error("LLM returned invalid JSON for prospect %d: %s", prospect.id, exc)
            return None

        # Parse signals
        signals = []
        for s in data.get("signals", []):
            try:
                signals.append(ProspectSignal(**s))
            except Exception:
                pass

        # Parse outreach angles
        angles = []
        for a in data.get("outreach_angles", []):
            try:
                angles.append(OutreachAngle(**a))
            except Exception:
                pass

        try:
            dossier = ProspectDossier(
                profile_url=prospect.linkedin_profile_url,
                name=prospect.name,
                role_summary=data.get("role_summary", ""),
                company_context=data.get("company_context", ""),
                relevance_verdict=data.get("relevance_verdict", ""),
                relevance_bucket=data.get("relevance_bucket", "skip"),
                confidence_score=int(data.get("confidence_score", 0)),
                signals=signals,
                outreach_angles=angles,
                recommended_action=data.get("recommended_action", "skip"),
                priority_rank=int(data.get("priority_rank", 99)),
                reasoning=data.get("reasoning", ""),
                caveats=data.get("caveats", []),
            )
            return dossier
        except (ValidationError, Exception) as exc:
            logger.error("Dossier validation failed for prospect %d: %s", prospect.id, exc)
            return None

    # ------------------------------------------------------------------
    # DB helpers
    # ------------------------------------------------------------------

    def _load_pending_prospects(self) -> List[ProspectORM]:
        with session_scope() as session:
            rows = (
                session.query(ProspectORM)
                .filter(
                    ProspectORM.dossier_status == "pending",
                    ProspectORM.assessed_at_utc.isnot(None),
                    ProspectORM.agent_id == self._agent_id,
                )
                .order_by(ProspectORM.contact_relevance_score.desc())
                .all()
            )
            session.expunge_all()
            return rows

    def _get_company(
        self, company_id: int, cache: Dict[int, CompanyResearchORM]
    ) -> Optional[CompanyResearchORM]:
        if company_id in cache:
            return cache[company_id]
        with session_scope() as session:
            row = (
                session.query(CompanyResearchORM)
                .filter_by(id=company_id, agent_id=self._agent_id)
                .one_or_none()
            )
            if row:
                session.expunge(row)
                cache[company_id] = row
            return row

    def _save_dossier(self, prospect_id: int, dossier: ProspectDossier) -> None:
        with session_scope() as session:
            row = (
                session.query(ProspectORM)
                .filter_by(id=prospect_id, agent_id=self._agent_id)
                .one_or_none()
            )
            if not row:
                return
            row.dossier_status = "completed"
            row.dossier_json = dossier.model_dump_json()

    def _mark_skipped(self, prospect_id: int) -> None:
        with session_scope() as session:
            row = (
                session.query(ProspectORM)
                .filter_by(id=prospect_id, agent_id=self._agent_id)
                .one_or_none()
            )
            if row:
                row.dossier_status = "skipped"

