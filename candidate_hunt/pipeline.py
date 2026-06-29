from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from agents.config_resolver import AgentConfigResolver
from agents.runtime import RuntimeExecutor, RuntimePolicy
from config import AppConfig
from candidate_hunt.profile_extractor import LinkedInCandidateProfileExtractor
from candidate_hunt.query_builder import CandidateQueryBuilder
from candidate_hunt.schemas import CandidateSearchCard, JobContext
from candidate_hunt.scoring import CandidateAssessor
from candidate_hunt.search_extractor import LinkedInCandidateSearchExtractor
from db import CandidateProfileORM, NaukriJobORM, resolve_agent_id, session_scope
from intelligence.dossier_generator import LLMClient
from research.linkedin_browser import ChromeLauncher, LinkedInBrowser
from utils.logging import build_logger
from utils.prompt_loader import render_prompt

logger = build_logger("prospect.candidate_hunt.pipeline")


class CandidateHuntPipeline:
    """
    Independent LinkedIn candidate-hunting service.

    Flow:
      1. Load pending jobs from DB
      2. Build role-keyword query variants (LLM + deterministic fallback)
      3. Ingest LinkedIn people search cards to candidate_profiles (minimal fields)
      4. Enrich selected candidate profiles
      5. Assess job-seeking + JD relevance with evidence-backed scoring
      6. Persist staged outcomes and job-level run status
    """

    def __init__(
        self,
        config: AppConfig,
        *,
        agent_id: Optional[int] = None,
        agent_context: Optional[Dict[str, Any]] = None,
        runtime_mode: Optional[str] = None,
    ) -> None:
        self._cfg = config
        self._agent_id = resolve_agent_id(
            agent_id=agent_id,
            default_agent_key=config.agent_runtime.default_agent_key,
        )
        resolved = AgentConfigResolver(config).resolve(agent_id=self._agent_id)
        self._agent_context = dict(resolved.prompt_context)
        if agent_context:
            self._agent_context.update(agent_context)
        self._runtime_mode = (runtime_mode or resolved.runtime_mode or config.agent_runtime.mode).strip().lower()
        if self._runtime_mode not in {"deterministic", "autonomous"}:
            self._runtime_mode = "deterministic"
        self._runtime_executor = RuntimeExecutor()
        self._runtime_policy = RuntimePolicy(
            mode=self._runtime_mode,
            max_tool_calls_per_run=config.agent_runtime.max_tool_calls_per_run,
            max_run_minutes=config.agent_runtime.max_run_minutes,
            allow_fallback=config.agent_runtime.autonomous_allow_fallback,
            allowed_tools=list(resolved.enabled_tools),
            metadata={"agent_id": self._agent_id, "agent_key": resolved.agent_key},
        )
        self._query_keyword_overrides = resolved.keywords_by_type.get("people_search", [])
        self._launcher = ChromeLauncher(config.chrome)
        self._browser = LinkedInBrowser(config.chrome)

        self._query_builder = CandidateQueryBuilder(
            config.llm,
            config.candidate_hunt,
            prompt_context=self._agent_context,
            keyword_overrides=self._query_keyword_overrides,
        )
        self._assessor = CandidateAssessor(config.llm, prompt_context=self._agent_context)
        self._ingest_llm: Optional[LLMClient] = None
        if config.llm.api_key:
            try:
                self._ingest_llm = LLMClient(config.llm)
            except Exception as exc:
                logger.warning("Candidate ingestion LLM gate disabled, using rule-only mode: %s", exc)

    def run(self) -> Dict[str, int]:
        stats = {
            "jobs_selected": 0,
            "jobs_completed": 0,
            "jobs_failed": 0,
            "queries_generated": 0,
            "jobs_query_mode_llm": 0,
            "jobs_query_mode_heuristic": 0,
            "candidates_ingested": 0,
            "candidates_updated": 0,
            "cards_fetched": 0,
            "cards_skipped_duplicate": 0,
            "cards_rejected_by_gate": 0,
            "cards_accepted_by_rule": 0,
            "cards_accepted_by_llm": 0,
            "cards_accepted_by_fallback": 0,
            "profiles_enriched": 0,
            "profiles_scored": 0,
        }

        if not self._cfg.candidate_hunt.enabled:
            logger.info("Candidate hunt is disabled via CANDIDATE_HUNT_ENABLED.")
            return stats

        self._launcher.launch_if_needed()
        self._browser.start()

        try:
            self._browser.ensure_logged_in()
            jobs = self._load_pending_jobs()
            stats["jobs_selected"] = len(jobs)
            logger.info("Candidate hunt: %d job(s) selected.", len(jobs))

            if not jobs:
                return stats

            assert self._browser.page is not None
            search_extractor = LinkedInCandidateSearchExtractor(
                self._browser.page,
                auth_handler=self._browser.ensure_logged_in,
            )
            profile_extractor = LinkedInCandidateProfileExtractor(
                self._browser.page,
                auth_handler=self._browser.ensure_logged_in,
            )
            search_extractor.configure_timing(
                navigation_timeout_ms=self._cfg.candidate_hunt.navigation_timeout_ms,
                page_settle_ms=self._cfg.candidate_hunt.page_settle_ms,
                polite_delay_sec=self._cfg.candidate_hunt.polite_delay_sec,
            )
            profile_extractor.configure_timing(
                navigation_timeout_ms=self._cfg.candidate_hunt.navigation_timeout_ms,
                page_settle_ms=self._cfg.candidate_hunt.page_settle_ms,
                polite_delay_sec=self._cfg.candidate_hunt.polite_delay_sec,
            )

            for job in jobs:
                run_id = uuid.uuid4().hex[:16]
                try:
                    if self._runtime_mode == "autonomous":
                        exec_result = self._runtime_executor.execute(
                            policy=self._runtime_policy,
                            context={"job_id": job.id, "search_run_id": run_id, "agent_id": self._agent_id},
                            deterministic_handler=lambda _ctx: self._execute_job_deterministic(
                                job,
                                run_id,
                                search_extractor,
                                profile_extractor,
                                stats,
                            ),
                        )
                        if not exec_result.ok:
                            raise RuntimeError(exec_result.error or "autonomous execution failed")
                    else:
                        self._execute_job_deterministic(
                            job,
                            run_id,
                            search_extractor,
                            profile_extractor,
                            stats,
                        )
                    stats["jobs_completed"] += 1
                except Exception as exc:
                    logger.error("Candidate hunt job failed for job_id=%s: %s", job.id, exc, exc_info=True)
                    self._mark_job_failed(job.id, str(exc))
                    stats["jobs_failed"] += 1

        finally:
            self._browser.stop()

        logger.info("Candidate hunt complete. Stats: %s", stats)
        return stats

    def _execute_job_deterministic(
        self,
        job: NaukriJobORM,
        run_id: str,
        search_extractor: LinkedInCandidateSearchExtractor,
        profile_extractor: LinkedInCandidateProfileExtractor,
        stats: Dict[str, int],
    ) -> Dict[str, Any]:
        self._process_job(job, run_id, search_extractor, profile_extractor, stats)
        return {"ok": True, "job_id": job.id}

    def _process_job(
        self,
        job: NaukriJobORM,
        run_id: str,
        search_extractor: LinkedInCandidateSearchExtractor,
        profile_extractor: LinkedInCandidateProfileExtractor,
        stats: Dict[str, int],
    ) -> None:
        self._mark_job_in_progress(job.id)
        job_ctx = self._to_job_context(job)

        query_plan = self._query_builder.build(job_ctx)
        search_queries = query_plan.final_queries[: self._cfg.candidate_hunt.max_query_variants]
        stats["queries_generated"] += len(search_queries)
        mode = "llm" if query_plan.llm_used else "heuristic"
        stats["jobs_query_mode_llm" if query_plan.llm_used else "jobs_query_mode_heuristic"] += 1

        if not search_queries:
            self._mark_job_failed(job.id, "query_generation_produced_zero_queries")
            return

        logger.info(
            "job_id=%s query_generation_mode=%s generated %d query variants.",
            job.id,
            mode,
            len(search_queries),
        )

        seen_for_job: set[str] = set()
        for query in search_queries:
            remaining = self._cfg.candidate_hunt.max_candidates_per_job - len(seen_for_job)
            if remaining <= 0:
                break

            cards, notes = search_extractor.collect(
                search_query=query,
                max_pages=self._cfg.candidate_hunt.max_pages_per_query,
                max_candidates=remaining,
            )
            if notes:
                logger.debug("job_id=%s query='%s' notes=%s", job.id, query, notes)

            stats["cards_fetched"] += len(cards)
            query_counts = {
                "fetched": len(cards),
                "skipped_duplicate": 0,
                "rejected_by_gate": 0,
                "accepted_by_rule": 0,
                "accepted_by_llm": 0,
                "accepted_by_fallback": 0,
                "inserted": 0,
                "updated": 0,
            }
            ranked_for_fallback: List[Tuple[int, CandidateSearchCard]] = []

            for card in cards:
                key = card.linkedin_profile_url.lower().rstrip("/")
                if key in seen_for_job:
                    stats["cards_skipped_duplicate"] += 1
                    query_counts["skipped_duplicate"] += 1
                    continue

                should_ingest, decision, rule_score = self._should_ingest_card(job_ctx, card)
                if not should_ingest:
                    stats["cards_rejected_by_gate"] += 1
                    query_counts["rejected_by_gate"] += 1
                    ranked_for_fallback.append((rule_score, card))
                    continue

                seen_for_job.add(key)
                upserted = self._upsert_candidate_card(job_id=job.id, run_id=run_id, query=query, card=card)
                if upserted:
                    stats["candidates_ingested"] += 1
                    query_counts["inserted"] += 1
                else:
                    stats["candidates_updated"] += 1
                    query_counts["updated"] += 1

                if decision == "rule_pass":
                    stats["cards_accepted_by_rule"] += 1
                    query_counts["accepted_by_rule"] += 1
                elif decision == "llm_pass":
                    stats["cards_accepted_by_llm"] += 1
                    query_counts["accepted_by_llm"] += 1

            if query_counts["inserted"] == 0 and query_counts["updated"] == 0 and ranked_for_fallback:
                ranked_for_fallback.sort(key=lambda x: x[0], reverse=True)
                fallback_cap = min(3, remaining)
                for rule_score, card in ranked_for_fallback[:fallback_cap]:
                    key = card.linkedin_profile_url.lower().rstrip("/")
                    if key in seen_for_job:
                        continue
                    seen_for_job.add(key)
                    upserted = self._upsert_candidate_card(job_id=job.id, run_id=run_id, query=query, card=card)
                    if upserted:
                        stats["candidates_ingested"] += 1
                        query_counts["inserted"] += 1
                    else:
                        stats["candidates_updated"] += 1
                        query_counts["updated"] += 1
                    stats["cards_accepted_by_fallback"] += 1
                    query_counts["accepted_by_fallback"] += 1
                    logger.debug(
                        "job_id=%s query='%s' fallback-ingested card url=%s rule_score=%s",
                        job.id,
                        query,
                        card.linkedin_profile_url,
                        rule_score,
                    )

            logger.info(
                "job_id=%s query='%s' fetched=%d inserted=%d updated=%d rejected=%d dup=%d rule=%d llm=%d fallback=%d",
                job.id,
                query,
                query_counts["fetched"],
                query_counts["inserted"],
                query_counts["updated"],
                query_counts["rejected_by_gate"],
                query_counts["skipped_duplicate"],
                query_counts["accepted_by_rule"],
                query_counts["accepted_by_llm"],
                query_counts["accepted_by_fallback"],
            )

        if not seen_for_job:
            logger.warning(
                "job_id=%s had fetched cards but none were accepted for persistence after gate+fallback.",
                job.id,
            )
            self._mark_job_completed(job.id)
            return

        candidates = self._load_candidates_for_enrichment(job.id, run_id)
        max_enrich = self._cfg.candidate_hunt.max_profiles_to_enrich

        for row in candidates[:max_enrich]:
            card = CandidateSearchCard(
                linkedin_profile_url=row.linkedin_profile_url,
                linkedin_public_id=row.linkedin_public_id,
                full_name=row.full_name,
                headline=row.headline,
                location_text=row.location_text,
                current_summary_text=row.current_summary_text,
                connection_degree=row.connection_degree,
                is_open_to_work=bool(row.is_open_to_work),
                search_page_no=int(row.search_page_no or 0),
                position_on_page=int(row.position_on_page or 0),
                source_search_url=row.source_search_url or "",
            )

            try:
                profile = profile_extractor.extract(row.linkedin_profile_url)
                stats["profiles_enriched"] += 1

                assessment = self._assessor.assess(job_ctx, card, profile)
                stats["profiles_scored"] += 1

                self._save_profile_assessment(
                    candidate_id=row.id,
                    profile=profile,
                    assessment=assessment,
                )
            except Exception as exc:
                logger.warning(
                    "Profile enrichment/scoring failed for candidate_id=%s url=%s: %s",
                    row.id,
                    row.linkedin_profile_url,
                    exc,
                )
                self._mark_candidate_failed(row.id, "profile_scoring", str(exc))

        self._mark_job_completed(job.id)

    def _to_job_context(self, job: NaukriJobORM) -> JobContext:
        return JobContext(
            job_id=job.id,
            title=job.title,
            company_name=job.company_name,
            location_text=job.location_text,
            experience_text=job.experience_text,
            industry=job.industry,
            role=job.role,
            role_category=job.role_category,
            job_description_text=job.job_description_text,
            skills=job.skills,
        )

    def _load_pending_jobs(self) -> List[NaukriJobORM]:
        with session_scope() as session:
            rows = (
                session.query(NaukriJobORM)
                .filter(
                    NaukriJobORM.agent_id == self._agent_id,
                    (NaukriJobORM.candidate_hunt_status.is_(None))
                    | (NaukriJobORM.candidate_hunt_status.in_(["pending", "failed"])),
                    NaukriJobORM.candidate_hunt_attempts < 3,
                )
                .order_by(NaukriJobORM.id.desc())
                .limit(self._cfg.candidate_hunt.job_batch_size)
                .all()
            )
            session.expunge_all()
            return rows

    def _load_candidates_for_enrichment(self, job_id: int, run_id: str) -> List[CandidateProfileORM]:
        with session_scope() as session:
            rows = (
                session.query(CandidateProfileORM)
                .filter(
                    CandidateProfileORM.agent_id == self._agent_id,
                    CandidateProfileORM.job_id == job_id,
                    CandidateProfileORM.search_run_id == run_id,
                    CandidateProfileORM.profile_status.in_(["queued", "profile_failed"]),
                    CandidateProfileORM.attempts < max(1, self._cfg.candidate_hunt.profile_retry_limit),
                )
                .order_by(
                    CandidateProfileORM.is_open_to_work.desc(),
                    CandidateProfileORM.search_page_no.asc(),
                    CandidateProfileORM.position_on_page.asc(),
                )
                .all()
            )
            session.expunge_all()
            return rows

    def _upsert_candidate_card(self, job_id: int, run_id: str, query: str, card: CandidateSearchCard) -> bool:
        now = self._now_utc()
        with session_scope() as session:
            row = (
                session.query(CandidateProfileORM)
                .filter_by(
                    agent_id=self._agent_id,
                    job_id=job_id,
                    linkedin_profile_url=card.linkedin_profile_url,
                )
                .one_or_none()
            )

            if row is None:
                row = CandidateProfileORM(
                    job_id=job_id,
                    agent_id=self._agent_id,
                    search_run_id=run_id,
                    search_query=self._clip(query, 500),
                    linkedin_profile_url=self._clip(card.linkedin_profile_url, 1000) or card.linkedin_profile_url,
                    linkedin_public_id=self._clip(card.linkedin_public_id, 255),
                    full_name=self._clip(card.full_name, 255),
                    headline=self._clip(card.headline, 500),
                    location_text=self._clip(card.location_text, 255),
                    current_summary_text=self._clip(card.current_summary_text, 500),
                    connection_degree=self._clip(card.connection_degree, 20),
                    is_open_to_work=card.is_open_to_work,
                    search_page_no=card.search_page_no,
                    position_on_page=card.position_on_page,
                    source_search_url=self._clip(card.source_search_url, 1200),
                    discovered_at_utc=now,
                    extraction_stage="search_ingestion",
                    profile_status="queued",
                    stage_status_json=json.dumps({"search_ingestion": "success"}),
                    stage_errors_json=json.dumps({}),
                    attempts=0,
                    created_at_utc=now,
                    updated_at_utc=now,
                )
                session.add(row)
                return True

            row.search_run_id = self._clip(run_id, 64) or run_id
            row.search_query = self._clip(query, 500)
            row.linkedin_public_id = self._clip(card.linkedin_public_id, 255) or row.linkedin_public_id
            row.full_name = self._clip(card.full_name, 255) or row.full_name
            row.headline = self._clip(card.headline, 500) or row.headline
            row.location_text = self._clip(card.location_text, 255) or row.location_text
            row.current_summary_text = self._clip(card.current_summary_text, 500) or row.current_summary_text
            row.connection_degree = self._clip(card.connection_degree, 20) or row.connection_degree
            row.is_open_to_work = card.is_open_to_work or bool(row.is_open_to_work)
            row.search_page_no = card.search_page_no
            row.position_on_page = card.position_on_page
            row.source_search_url = self._clip(card.source_search_url, 1200)
            row.extraction_stage = "search_ingestion"
            if row.profile_status in {"failed", "profile_failed", "scoring_failed"}:
                row.profile_status = "queued"
            stage_status = self._load_json_object(row.stage_status_json)
            stage_status["search_ingestion"] = "success"
            row.stage_status_json = json.dumps(stage_status)
            row.updated_at_utc = now
            return False

    def _save_profile_assessment(self, candidate_id: int, profile, assessment) -> None:
        now = self._now_utc()
        with session_scope() as session:
            row = session.query(CandidateProfileORM).filter_by(id=candidate_id).one_or_none()
            if row is None:
                return
            if int(row.agent_id or 0) != int(self._agent_id):
                return

            row.full_name = self._clip(profile.profile_name, 255) or row.full_name
            row.headline = self._clip(profile.profile_headline, 500) or row.headline
            row.location_text = self._clip(profile.profile_location, 255) or row.location_text
            row.profile_name = self._clip(profile.profile_name, 255) or row.profile_name
            row.profile_headline = self._clip(profile.profile_headline, 500) or row.profile_headline
            row.profile_location = self._clip(profile.profile_location, 255) or row.profile_location
            row.profile_about_text = profile.profile_about_text
            row.current_title = self._clip(profile.current_title, 500)
            row.current_company = self._clip(profile.current_company, 500)
            row.experiences_json = json.dumps(profile.experiences, ensure_ascii=False) if profile.experiences else None
            row.education_json = json.dumps(profile.education, ensure_ascii=False) if profile.education else None
            row.skills_json = json.dumps(profile.skills, ensure_ascii=False) if profile.skills else None
            row.certifications_json = json.dumps(profile.certifications, ensure_ascii=False) if profile.certifications else None
            row.activity_json = json.dumps(profile.activity, ensure_ascii=False) if profile.activity else None
            row.contact_points_json = json.dumps(profile.contact_points, ensure_ascii=False) if profile.contact_points else None
            row.resume_urls_json = json.dumps(profile.resume_urls, ensure_ascii=False) if profile.resume_urls else None
            row.resume_text = profile.resume_text

            row.job_seeking_status = self._clip(assessment.job_seeking_status, 50)
            row.job_seeking_score = assessment.job_seeking_score
            row.confidence_score = assessment.confidence_score
            row.top_evidence_json = json.dumps(assessment.top_evidence, ensure_ascii=False)
            row.negative_evidence_json = json.dumps(assessment.negative_evidence, ensure_ascii=False)
            row.ambiguity_notes_json = json.dumps(assessment.ambiguity_notes, ensure_ascii=False)

            row.jd_relevance_score = assessment.jd_relevance_score
            row.jd_dimension_scores_json = json.dumps(assessment.jd_dimension_scores, ensure_ascii=False)
            row.missing_critical_requirements_json = json.dumps(
                assessment.missing_critical_requirements,
                ensure_ascii=False,
            )
            row.llm_summary_text = assessment.llm_summary_text
            row.llm_payload_json = (
                json.dumps(assessment.llm_payload, ensure_ascii=False)
                if assessment.llm_payload
                else None
            )

            row.extraction_stage = "scoring"
            row.profile_status = "completed"
            row.profile_extracted_at_utc = now
            row.scored_at_utc = now
            row.completed_at_utc = now
            row.failure_reason = None
            row.updated_at_utc = now

            stage_status = self._load_json_object(row.stage_status_json)
            stage_status["profile_extraction"] = "success"
            stage_status["activity_extraction"] = "success" if profile.activity else "partial"
            stage_status["scoring"] = "success"
            stage_status["persistence"] = "success"
            row.stage_status_json = json.dumps(stage_status, ensure_ascii=False)

            stage_errors = self._load_json_object(row.stage_errors_json)
            for key in ["profile_extraction", "activity_extraction", "scoring", "persistence"]:
                stage_errors.pop(key, None)
            row.stage_errors_json = json.dumps(stage_errors, ensure_ascii=False)

    def _mark_candidate_failed(self, candidate_id: int, stage: str, reason: str) -> None:
        now = self._now_utc()
        with session_scope() as session:
            row = session.query(CandidateProfileORM).filter_by(id=candidate_id).one_or_none()
            if row is None:
                return
            if int(row.agent_id or 0) != int(self._agent_id):
                return
            row.profile_status = "scoring_failed" if stage == "scoring" else "profile_failed"
            row.extraction_stage = stage
            row.failure_reason = (reason or "unknown")[:2000]
            row.attempts = int(row.attempts or 0) + 1
            row.updated_at_utc = now

            stage_status = self._load_json_object(row.stage_status_json)
            stage_status[stage] = "failed"
            row.stage_status_json = json.dumps(stage_status, ensure_ascii=False)

            stage_errors = self._load_json_object(row.stage_errors_json)
            stage_errors[stage] = (reason or "unknown")[:1000]
            row.stage_errors_json = json.dumps(stage_errors, ensure_ascii=False)

    def _mark_job_in_progress(self, job_id: int) -> None:
        now = self._now_utc()
        with session_scope() as session:
            row = session.query(NaukriJobORM).filter_by(id=job_id, agent_id=self._agent_id).one_or_none()
            if row:
                row.candidate_hunt_status = "in_progress"
                row.candidate_hunt_attempts = int(row.candidate_hunt_attempts or 0) + 1
                row.candidate_hunt_failure_reason = None
                row.candidate_hunted_at_utc = None

    def _mark_job_completed(self, job_id: int) -> None:
        with session_scope() as session:
            row = session.query(NaukriJobORM).filter_by(id=job_id, agent_id=self._agent_id).one_or_none()
            if row:
                row.candidate_hunt_status = "completed"
                row.candidate_hunt_failure_reason = None
                row.candidate_hunted_at_utc = self._now_utc()

    def _mark_job_failed(self, job_id: int, reason: str) -> None:
        with session_scope() as session:
            row = session.query(NaukriJobORM).filter_by(id=job_id, agent_id=self._agent_id).one_or_none()
            if row:
                row.candidate_hunt_status = "failed"
                row.candidate_hunt_failure_reason = (reason or "unknown")[:2000]

    def _now_utc(self) -> datetime:
        return datetime.now(timezone.utc).replace(tzinfo=None)

    def _load_json_object(self, raw: Optional[str]) -> Dict[str, str]:
        if not raw:
            return {}
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}

    def _clip(self, value: Optional[str], max_len: int) -> Optional[str]:
        if value is None:
            return None
        text = str(value).strip()
        if not text:
            return None
        if len(text) <= max_len:
            return text
        return text[:max_len]

    def _card_relevance_score(self, job: JobContext, card: CandidateSearchCard) -> int:
        target_tokens = self._tokenize(
            " ".join(
                filter(
                    None,
                    [
                        job.title or "",
                        job.role or "",
                        job.role_category or "",
                        " ".join(job.skills[:12]),
                    ],
                )
            )
        )
        if not target_tokens:
            return 50

        card_tokens = self._tokenize(
            " ".join(
                filter(
                    None,
                    [
                        card.headline or "",
                        card.current_summary_text or "",
                        card.full_name or "",
                    ],
                )
            )
        )
        if not card_tokens:
            return 20

        overlap = len(target_tokens & card_tokens)
        coverage = overlap / max(1, min(len(target_tokens), len(card_tokens)))
        score = int(round(coverage * 100))

        card_text = f"{card.headline or ''} {card.current_summary_text or ''}".lower()
        if re.search(r"\b(intern|fresher|student|trainee)\b", card_text):
            score -= 25
        if card.is_open_to_work:
            score += 8
        return max(0, min(100, score))

    def _should_ingest_card(self, job: JobContext, card: CandidateSearchCard) -> Tuple[bool, str, int]:
        rule_score = self._card_relevance_score(job, card)
        threshold = int(self._cfg.candidate_hunt.min_card_relevance_score)
        if rule_score >= threshold:
            return True, "rule_pass", rule_score

        llm_decision = self._llm_ingest_decision(job, card, rule_score, threshold)
        if llm_decision is not None:
            if bool(llm_decision.get("ingest")):
                return True, "llm_pass", rule_score
            return False, "llm_reject", rule_score

        relaxed_floor = max(8, threshold - 15)
        if rule_score >= relaxed_floor:
            return True, "relaxed_rule_floor", rule_score
        return False, "rule_reject", rule_score

    def _llm_ingest_decision(
        self,
        job: JobContext,
        card: CandidateSearchCard,
        rule_score: int,
        threshold: int,
    ) -> Optional[Dict[str, Any]]:
        if self._ingest_llm is None:
            return None

        system = render_prompt(
            "candidate_ingest_gate_system",
            agent_context=self._agent_context,
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
                    "role": job.role,
                    "role_category": job.role_category,
                    "skills": job.skills[:15],
                    "job_description_excerpt": (job.job_description_text or "")[:1500],
                },
                "candidate_card": {
                    "linkedin_public_id": card.linkedin_public_id,
                    "full_name": card.full_name,
                    "headline": card.headline,
                    "location_text": card.location_text,
                    "current_summary_text": card.current_summary_text,
                    "connection_degree": card.connection_degree,
                    "is_open_to_work": card.is_open_to_work,
                    "profile_url": card.linkedin_profile_url,
                },
                "rule_gate": {
                    "rule_score": rule_score,
                    "threshold": threshold,
                },
            },
            ensure_ascii=False,
        )
        try:
            raw = self._ingest_llm.chat(system, user)
            parsed = json.loads(raw)
            if not isinstance(parsed, dict):
                return None
            parsed["ingest"] = bool(parsed.get("ingest"))
            parsed["confidence"] = self._coerce_int(parsed.get("confidence"), 50)
            parsed["estimated_relevance_score"] = self._coerce_int(
                parsed.get("estimated_relevance_score"),
                rule_score,
            )
            parsed["reason"] = self._clip(parsed.get("reason"), 300) or ""
            logger.debug(
                "LLM ingest gate decision job_id=%s profile=%s ingest=%s confidence=%s est_score=%s reason=%s",
                job.job_id,
                card.linkedin_profile_url,
                parsed["ingest"],
                parsed["confidence"],
                parsed["estimated_relevance_score"],
                parsed["reason"],
            )
            return parsed
        except Exception as exc:
            logger.warning(
                "LLM ingest gate failed for job_id=%s profile=%s: %s",
                job.job_id,
                card.linkedin_profile_url,
                exc,
            )
            return None

    def _coerce_int(self, value: Any, fallback: int) -> int:
        try:
            return max(0, min(100, int(value)))
        except Exception:
            return fallback

    def _tokenize(self, text: str) -> set[str]:
        words = re.findall(r"[a-z0-9]+", (text or "").lower())
        stop = {
            "and", "or", "the", "a", "an", "of", "to", "in", "for", "with", "at", "on", "by", "from"
        }
        return {w for w in words if len(w) > 2 and w not in stop}
