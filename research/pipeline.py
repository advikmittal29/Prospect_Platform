"""
Research pipeline orchestrator.

For each unresearched company in CompanyResearchORM (status='pending'):
  1. Find its LinkedIn company URL (LinkedInCompanyFinder)
  2. Extract company brief
  3. Search for relevant people (LinkedInPeopleFinder)
  4. Deep-assess top N profiles (LinkedInProfileAssessor)
  5. Persist everything to DB
  6. Mark associated NaukriJobORM rows as researched
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import func

from config import AppConfig
from db import (
    CompanyResearchORM,
    NaukriJobORM,
    ProspectORM,
    resolve_agent_id,
    session_scope,
)
from research.linkedin_browser import ChromeLauncher, LinkedInBrowser
from research.company_finder import LinkedInCompanyFinder
from research.people_finder import LinkedInPeopleFinder, ProspectCandidate
from research.profile_assessor import LinkedInProfileAssessor, ProfileAssessment
from utils.logging import build_logger

logger = build_logger("prospect.research.pipeline")


class ResearchPipeline:
    """
    Top-level orchestrator for the company + people research phase.
    Designed to run as a scheduled job.
    """

    def __init__(
        self,
        config: AppConfig,
        *,
        agent_id: Optional[int] = None,
        agent_context: Optional[Dict[str, Any]] = None,
        prospect_keywords: Optional[List[str]] = None,
    ) -> None:
        self._cfg = config
        self._agent_id = resolve_agent_id(
            agent_id=agent_id,
            default_agent_key=config.agent_runtime.default_agent_key,
        )
        self._prompt_context = dict(agent_context or {})
        self._prospect_keywords = [
            str(x).strip() for x in (prospect_keywords or []) if str(x).strip()
        ]
        self._launcher = ChromeLauncher(config.chrome)
        self._browser = LinkedInBrowser(config.chrome)

    # ------------------------------------------------------------------
    # Entry point
    # ------------------------------------------------------------------

    def run(self) -> dict:
        stats = {
            "companies_processed": 0,
            "companies_found": 0,
            "companies_failed": 0,
            "prospects_saved": 0,
            "profiles_assessed": 0,
        }

        # Ensure Chrome is running
        self._launcher.launch_if_needed()
        self._browser.start()

        try:
            # Validate session at start; auto-login using DB credentials if needed.
            self._browser.ensure_logged_in()

            companies = self._load_pending_companies()
            logger.info("Research pipeline: %d companies pending.", len(companies))

            if not companies:
                logger.info("Nothing to research.")
                return stats

            company_finder = LinkedInCompanyFinder(
                self._browser.page,
                auth_handler=self._browser.ensure_logged_in,
            )
            people_finder = LinkedInPeopleFinder(
                self._browser.page,
                auth_handler=self._browser.ensure_logged_in,
            )
            profile_assessor = LinkedInProfileAssessor(
                self._browser.page,
                auth_handler=self._browser.ensure_logged_in,
                llm_config=self._cfg.llm,
                prompt_context=self._prompt_context,
            )

            for company_row in companies:
                try:
                    self._process_company(
                        company_row,
                        company_finder,
                        people_finder,
                        profile_assessor,
                        stats,
                    )
                except Exception as exc:
                    logger.error("Unhandled error on company %s: %s", company_row.company_name, exc, exc_info=True)
                    self._mark_company_failed(company_row.id, str(exc))
                    stats["companies_failed"] += 1

        finally:
            self._browser.stop()

        logger.info("Research pipeline complete. Stats: %s", stats)
        return stats

    # ------------------------------------------------------------------
    # Company processing
    # ------------------------------------------------------------------

    def _process_company(
        self,
        company_row: CompanyResearchORM,
        company_finder: LinkedInCompanyFinder,
        people_finder: LinkedInPeopleFinder,
        profile_assessor: LinkedInProfileAssessor,
        stats: dict,
    ) -> None:
        company_name = company_row.company_name
        logger.info("Processing company: %s", company_name)

        # Re-check auth because long runs can lose session.
        self._browser.ensure_logged_in()

        self._mark_company_in_progress(company_row.id)
        stats["companies_processed"] += 1

        # Step 1: Resolve LinkedIn company URL
        if company_row.linkedin_url:
            company_url = company_row.linkedin_url
            brief = company_finder.get_company_brief(company_url)
            confidence = float(company_row.linkedin_match_confidence or 100.0)
            matched_title = company_row.linkedin_matched_title
            stats["companies_found"] += 1
            logger.info("  Reusing existing LinkedIn URL: %s", company_url)
        else:
            result = company_finder.find(company_name)
            if result.status != "success" or not result.company_url:
                reason = f"company_not_found:{result.status}:{result.reason}"
                logger.warning("  %s  %s", company_name, reason)
                self._mark_company_failed(company_row.id, reason)
                stats["companies_failed"] += 1
                return
            company_url = result.company_url
            brief = result.company_brief
            confidence = result.confidence
            matched_title = result.matched_title
            stats["companies_found"] += 1
            logger.info("  Found: %s (confidence=%.0f)", company_url, result.confidence)

        # Step 2: Persist company data
        self._save_company_data(
            company_row.id,
            company_url=company_url,
            confidence=confidence,
            matched_title=matched_title,
            brief=brief,
        )

        # Step 3: Find people
        keywords = self._prospect_keywords or self._cfg.research.prospect_keywords
        people_result = people_finder.collect(
            company_url=company_url,
            keywords=keywords,
            max_results=self._cfg.research.max_prospects_per_company,
        )
        logger.info(
            "  Found %d prospect candidates (searched %d keywords).",
            len(people_result.prospects),
            len(people_result.searched_keywords),
        )

        if not people_result.prospects:
            self._mark_company_completed(company_row.id)
            self._mark_jobs_researched(company_name)
            return

        # Step 4: Save candidate stubs, then deep-assess top N
        company_brief_name = (brief.company_name if brief else None) or company_name

        top_for_assessment = people_result.prospects[: self._cfg.research.max_profiles_to_assess]

        for candidate in people_result.prospects:
            prospect_id = self._save_prospect_candidate(company_row.id, candidate)
            stats["prospects_saved"] += 1

        # Step 5: Deep profile assessment for top candidates
        for candidate in top_for_assessment:
            try:
                assessment = profile_assessor.assess(
                    profile_url=candidate.profile_url,
                    target_company_name=company_brief_name,
                    target_company_url=company_url,
                )
                self._update_prospect_with_assessment(
                    company_research_id=company_row.id,
                    profile_url=candidate.profile_url,
                    assessment=assessment,
                )
                stats["profiles_assessed"] += 1
                logger.debug(
                    "  Assessed %s: relevance=%s (%d)",
                    candidate.name or candidate.profile_url,
                    assessment.contact_relevance_bucket,
                    assessment.contact_relevance_score,
                )
            except Exception as exc:
                logger.warning("  Profile assessment failed for %s: %s", candidate.profile_url, exc)

        self._mark_company_completed(company_row.id)
        self._mark_jobs_researched(company_name)

    # ------------------------------------------------------------------
    # DB operations
    # ------------------------------------------------------------------

    def _load_pending_companies(self) -> List[CompanyResearchORM]:
        with session_scope() as session:
            rows = (
                session.query(CompanyResearchORM)
                .filter(
                    CompanyResearchORM.research_status == "pending",
                    CompanyResearchORM.attempts < 3,
                    CompanyResearchORM.agent_id == self._agent_id,
                )
                .order_by(CompanyResearchORM.created_at_utc)
                .limit(self._cfg.research.batch_size)
                .all()
            )
            session.expunge_all()
            return rows

    def _mark_company_in_progress(self, company_id: int) -> None:
        with session_scope() as session:
            row = session.query(CompanyResearchORM).filter_by(id=company_id).one_or_none()
            if row:
                row.research_status = "in_progress"
                row.attempts = (row.attempts or 0) + 1
                row.updated_at_utc = datetime.now(timezone.utc).replace(tzinfo=None)

    def _save_company_data(
        self,
        company_id: int,
        company_url: str,
        confidence: float,
        matched_title: Optional[str],
        brief,
    ) -> None:
        with session_scope() as session:
            row = session.query(CompanyResearchORM).filter_by(id=company_id).one_or_none()
            if not row:
                return
            row.linkedin_url = company_url
            row.linkedin_match_confidence = confidence
            row.linkedin_matched_title = matched_title
            row.updated_at_utc = datetime.now(timezone.utc).replace(tzinfo=None)
            if brief:
                row.tagline = brief.tagline
                row.industry = brief.industry
                row.location = brief.location
                row.employee_range = brief.employee_range
                row.followers = brief.followers
                if brief.company_name:
                    row.company_name = brief.company_name

    def _mark_company_completed(self, company_id: int) -> None:
        with session_scope() as session:
            row = session.query(CompanyResearchORM).filter_by(id=company_id).one_or_none()
            if row:
                row.research_status = "completed"
                row.updated_at_utc = datetime.now(timezone.utc).replace(tzinfo=None)

    def _mark_company_failed(self, company_id: int, reason: str) -> None:
        with session_scope() as session:
            row = session.query(CompanyResearchORM).filter_by(id=company_id).one_or_none()
            if row:
                row.research_status = "failed"
                row.failure_reason = reason[:1000]
                row.updated_at_utc = datetime.now(timezone.utc).replace(tzinfo=None)

    def _save_prospect_candidate(
        self, company_research_id: int, candidate: ProspectCandidate
    ) -> Optional[int]:
        with session_scope() as session:
            existing = (
                session.query(ProspectORM)
                .filter_by(
                    linkedin_profile_url=candidate.profile_url,
                    company_research_id=company_research_id,
                    agent_id=self._agent_id,
                )
                .one_or_none()
            )
            if existing:
                # Upsert non-destructive candidate fields to keep list fresh.
                existing.name = candidate.name or existing.name
                existing.headline = candidate.headline or existing.headline
                existing.connection_degree = candidate.connection_degree or existing.connection_degree
                existing.matched_keyword = candidate.matched_keyword or existing.matched_keyword
                existing.role_bucket = candidate.role_bucket or existing.role_bucket
                if candidate.confidence is not None:
                    existing.search_confidence = max(int(existing.search_confidence or 0), int(candidate.confidence))
                return existing.id

            row = ProspectORM(
                agent_id=self._agent_id,
                company_research_id=company_research_id,
                name=candidate.name,
                linkedin_profile_url=candidate.profile_url,
                headline=candidate.headline,
                connection_degree=candidate.connection_degree,
                matched_keyword=candidate.matched_keyword,
                role_bucket=candidate.role_bucket,
                search_confidence=candidate.confidence,
            )
            session.add(row)
            session.flush()
            return row.id

    def _update_prospect_with_assessment(
        self,
        company_research_id: int,
        profile_url: str,
        assessment: ProfileAssessment,
    ) -> None:
        with session_scope() as session:
            row = (
                session.query(ProspectORM)
                .filter_by(
                    linkedin_profile_url=profile_url,
                    company_research_id=company_research_id,
                    agent_id=self._agent_id,
                )
                .one_or_none()
            )
            if not row:
                return

            row.name = assessment.name or row.name
            row.headline = assessment.headline or row.headline
            row.location = assessment.location
            row.pronouns = assessment.pronouns
            row.connection_degree = assessment.connection_degree
            row.current_title = assessment.current_title_experience or assessment.current_title_topcard
            row.current_company = assessment.current_company_experience or assessment.current_company_topcard
            row.tenure_hint = assessment.tenure_hint
            row.about_text = assessment.about_text
            row.profile_summary_text = assessment.profile_summary_text
            row.experiences_json = (
                json.dumps(assessment.experiences, ensure_ascii=False)
                if assessment.experiences else None
            )
            row.recent_posts_json = (
                json.dumps(assessment.recent_posts, ensure_ascii=False)
                if assessment.recent_posts else None
            )
            row.llm_assessment_json = (
                json.dumps(assessment.llm_assessment, ensure_ascii=False)
                if assessment.llm_assessment else None
            )
            row.contact_info_available = assessment.contact_info_available
            row.message_available = assessment.message_available
            row.connect_available = assessment.connect_available
            row.company_match_confidence = assessment.company_match_confidence
            row.role_bucket = assessment.role_bucket
            row.outreach_feasibility_score = assessment.outreach_feasibility_score
            row.contact_relevance_score = assessment.contact_relevance_score
            row.contact_relevance_bucket = assessment.contact_relevance_bucket
            row.assessment_reasons_json = json.dumps(assessment.reasons)
            row.assessment_warnings_json = json.dumps(assessment.warnings)
            row.assessed_at_utc = datetime.now(timezone.utc).replace(tzinfo=None)
            row.agent_id = self._agent_id

    def _mark_jobs_researched(self, company_name: str) -> None:
        norm_name = self._norm_company_key(company_name)
        if not norm_name:
            return

        db_company_key = func.replace(
            func.replace(
                func.lower(func.ltrim(func.rtrim(func.coalesce(NaukriJobORM.company_name, "")))),
                ",",
                "",
            ),
            " ",
            "",
        )
        with session_scope() as session:
            rows = (
                session.query(NaukriJobORM)
                .filter(
                    NaukriJobORM.agent_id == self._agent_id,
                    db_company_key == norm_name,
                    NaukriJobORM.researched == False,
                )
                .all()
            )
            for row in rows:
                row.researched = True
                row.researched_at_utc = datetime.now(timezone.utc).replace(tzinfo=None)

    @staticmethod
    def _norm_company_key(value: Optional[str]) -> Optional[str]:
        if not value:
            return None
        return "".join(ch for ch in value.lower().strip() if ch not in {" ", ","}) or None


