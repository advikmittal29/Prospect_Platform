from __future__ import annotations

import json
import logging
import os
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Dict, Generator, List, Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    text,
)
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from config import (
    DatabaseConfig,
    reset_runtime_settings_cache,
)

logger = logging.getLogger("prospect.db")


class Base(DeclarativeBase):
    pass


# ---------------------------------------------------------------------------
# Agent control plane
# ---------------------------------------------------------------------------

class AgentDefinitionORM(Base):
    __tablename__ = "agent_definitions"
    __table_args__ = (
        UniqueConstraint("agent_key", name="uq_agent_definitions_agent_key"),
        Index("ix_agent_definitions_status", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    agent_key: Mapped[str] = mapped_column(String(100), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="active")
    agent_type: Mapped[str] = mapped_column(String(50), nullable=False, default="staffing")
    created_at_utc: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    updated_at_utc: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=lambda: datetime.now(timezone.utc)
    )


class AgentProfileORM(Base):
    __tablename__ = "agent_profiles"
    __table_args__ = (
        Index("ix_agent_profiles_agent_current", "agent_id", "is_current"),
        Index("ix_agent_profiles_created", "created_at_utc"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    agent_id: Mapped[int] = mapped_column(Integer, nullable=False)
    persona_title: Mapped[Optional[str]] = mapped_column(String(255))
    domain_focus: Mapped[Optional[str]] = mapped_column(String(500))
    service_offering: Mapped[Optional[str]] = mapped_column(String(500))
    target_buyer_roles_json: Mapped[Optional[str]] = mapped_column(Text)
    sales_objective: Mapped[Optional[str]] = mapped_column(Text)
    value_outcomes_json: Mapped[Optional[str]] = mapped_column(Text)
    icp_rules_json: Mapped[Optional[str]] = mapped_column(Text)
    targeting_policy_json: Mapped[Optional[str]] = mapped_column(Text)
    pipeline_policy_json: Mapped[Optional[str]] = mapped_column(Text)
    channel_policy_json: Mapped[Optional[str]] = mapped_column(Text)
    prompt_profile_json: Mapped[Optional[str]] = mapped_column(Text)
    runtime_policy_json: Mapped[Optional[str]] = mapped_column(Text)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    is_current: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at_utc: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    created_by: Mapped[Optional[str]] = mapped_column(String(100))


class AgentKeywordORM(Base):
    __tablename__ = "agent_keywords"
    __table_args__ = (
        UniqueConstraint("agent_id", "keyword_type", "keyword", name="uq_agent_keywords_triplet"),
        Index("ix_agent_keywords_agent_type", "agent_id", "keyword_type"),
        Index("ix_agent_keywords_active", "active"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    agent_id: Mapped[int] = mapped_column(Integer, nullable=False)
    keyword_type: Mapped[str] = mapped_column(String(50), nullable=False)
    keyword: Mapped[str] = mapped_column(String(255), nullable=False)
    weight: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at_utc: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    updated_at_utc: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=lambda: datetime.now(timezone.utc)
    )


class AgentRunORM(Base):
    __tablename__ = "agent_runs"
    __table_args__ = (
        Index("ix_agent_runs_agent_pipeline", "agent_id", "pipeline"),
        Index("ix_agent_runs_status", "status"),
        Index("ix_agent_runs_started", "started_at_utc"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    agent_id: Mapped[int] = mapped_column(Integer, nullable=False)
    pipeline: Mapped[str] = mapped_column(String(50), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="running")
    started_at_utc: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    ended_at_utc: Mapped[Optional[datetime]] = mapped_column(DateTime)
    triggered_by: Mapped[Optional[str]] = mapped_column(String(100))
    run_config_json: Mapped[Optional[str]] = mapped_column(Text)
    metrics_json: Mapped[Optional[str]] = mapped_column(Text)
    error_text: Mapped[Optional[str]] = mapped_column(Text)


class AgentToolORM(Base):
    __tablename__ = "agent_tools"
    __table_args__ = (
        UniqueConstraint("agent_id", "tool_name", name="uq_agent_tools_name"),
        Index("ix_agent_tools_agent_enabled", "agent_id", "enabled"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    agent_id: Mapped[int] = mapped_column(Integer, nullable=False)
    tool_name: Mapped[str] = mapped_column(String(100), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    budget_limit: Mapped[Optional[int]] = mapped_column(Integer)
    weight: Mapped[Optional[float]] = mapped_column(Float)
    policy_json: Mapped[Optional[str]] = mapped_column(Text)
    created_at_utc: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    updated_at_utc: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=lambda: datetime.now(timezone.utc)
    )


# ---------------------------------------------------------------------------
# Job ingestion table
# ---------------------------------------------------------------------------

class NaukriJobORM(Base):
    __tablename__ = "naukri_jobs"
    __table_args__ = (
        UniqueConstraint("agent_id", "canonical_job_url", name="uq_naukri_jobs_agent_canonical_url"),
        Index("ix_naukri_jobs_agent_id", "agent_id"),
        Index("ix_naukri_jobs_company_name", "company_name"),
        Index("ix_naukri_jobs_posted_date", "posted_date"),
        Index("ix_naukri_jobs_researched", "researched"),
        Index("ix_naukri_jobs_title_company_date", "title", "company_name", "posted_date"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    agent_id: Mapped[Optional[int]] = mapped_column(Integer)
    source: Mapped[str] = mapped_column(String(50), nullable=False)
    search_keyword: Mapped[Optional[str]] = mapped_column(String(255))
    search_location: Mapped[Optional[str]] = mapped_column(String(255))

    job_id: Mapped[Optional[str]] = mapped_column(String(100))
    title: Mapped[Optional[str]] = mapped_column(String(255))
    company_name: Mapped[Optional[str]] = mapped_column(String(255))
    posted_date: Mapped[Optional[str]] = mapped_column(String(20))
    posted_relative: Mapped[Optional[str]] = mapped_column(String(100))
    valid_through: Mapped[Optional[str]] = mapped_column(String(20))

    experience_text: Mapped[Optional[str]] = mapped_column(String(255))
    salary_text: Mapped[Optional[str]] = mapped_column(String(255))
    location_text: Mapped[Optional[str]] = mapped_column(String(500))
    employment_type: Mapped[Optional[str]] = mapped_column(String(255))
    industry: Mapped[Optional[str]] = mapped_column(String(255))
    department: Mapped[Optional[str]] = mapped_column(String(255))
    role: Mapped[Optional[str]] = mapped_column(String(255))
    role_category: Mapped[Optional[str]] = mapped_column(String(255))
    education: Mapped[Optional[str]] = mapped_column(String(255))

    skills_json: Mapped[Optional[str]] = mapped_column(Text)
    job_description_text: Mapped[Optional[str]] = mapped_column(Text)
    ai_summary: Mapped[Optional[str]] = mapped_column(Text)

    openings: Mapped[Optional[str]] = mapped_column(String(50))
    applicants: Mapped[Optional[str]] = mapped_column(String(50))
    company_rating: Mapped[Optional[str]] = mapped_column(String(50))
    company_review_count: Mapped[Optional[str]] = mapped_column(String(100))

    job_url: Mapped[str] = mapped_column(String(1000), nullable=False)
    canonical_job_url: Mapped[str] = mapped_column(String(500), nullable=False)

    extraction_confidence: Mapped[Optional[int]] = mapped_column(Integer)
    extraction_notes_json: Mapped[Optional[str]] = mapped_column(Text)
    content_hash: Mapped[Optional[str]] = mapped_column(String(64))

    fetched_at_utc: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    researched: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    researched_at_utc: Mapped[Optional[datetime]] = mapped_column(DateTime)
    candidate_hunt_status: Mapped[str] = mapped_column(
        String(50), nullable=False, default="pending"
    )
    candidate_hunt_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    candidate_hunt_failure_reason: Mapped[Optional[str]] = mapped_column(Text)
    candidate_hunted_at_utc: Mapped[Optional[datetime]] = mapped_column(DateTime)

    @property
    def skills(self) -> list[str]:
        if not self.skills_json:
            return []
        try:
            return json.loads(self.skills_json)
        except Exception:
            return []


# ---------------------------------------------------------------------------
# Company research table
# ---------------------------------------------------------------------------

class CompanyResearchORM(Base):
    __tablename__ = "company_research"
    __table_args__ = (
        UniqueConstraint(
            "agent_id",
            "linkedin_url",
            name="ux_company_research_agent_linkedin_url_nonnull",
        ),
        Index("ix_company_research_agent_id", "agent_id"),
        Index("ix_company_research_name", "company_name"),
        Index("ix_company_research_status", "research_status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    agent_id: Mapped[Optional[int]] = mapped_column(Integer)
    company_name: Mapped[str] = mapped_column(String(500), nullable=False)

    search_query: Mapped[Optional[str]] = mapped_column(String(500))
    linkedin_url: Mapped[Optional[str]] = mapped_column(String(500))
    linkedin_match_confidence: Mapped[Optional[float]] = mapped_column(Float)
    linkedin_matched_title: Mapped[Optional[str]] = mapped_column(String(500))

    tagline: Mapped[Optional[str]] = mapped_column(String(500))
    industry: Mapped[Optional[str]] = mapped_column(String(255))
    location: Mapped[Optional[str]] = mapped_column(String(255))
    employee_range: Mapped[Optional[str]] = mapped_column(String(100))
    followers: Mapped[Optional[str]] = mapped_column(String(100))

    research_status: Mapped[str] = mapped_column(String(50), nullable=False, default="pending")
    failure_reason: Mapped[Optional[str]] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    created_at_utc: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    updated_at_utc: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=lambda: datetime.now(timezone.utc)
    )


# ---------------------------------------------------------------------------
# Prospect / person table
# ---------------------------------------------------------------------------

class ProspectORM(Base):
    __tablename__ = "prospects"
    __table_args__ = (
        UniqueConstraint("linkedin_profile_url", "company_research_id", "agent_id", name="uq_prospect_profile_company_agent"),
        Index("ix_prospect_agent_id", "agent_id"),
        Index("ix_prospect_company_id", "company_research_id"),
        Index("ix_prospect_role_bucket", "role_bucket"),
        Index("ix_prospect_relevance_score", "contact_relevance_score"),
        Index("ix_prospect_dossier_status", "dossier_status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    agent_id: Mapped[Optional[int]] = mapped_column(Integer)
    company_research_id: Mapped[int] = mapped_column(Integer, nullable=False)

    name: Mapped[Optional[str]] = mapped_column(String(255))
    linkedin_profile_url: Mapped[str] = mapped_column(String(500), nullable=False)
    headline: Mapped[Optional[str]] = mapped_column(String(500))
    location: Mapped[Optional[str]] = mapped_column(String(255))
    pronouns: Mapped[Optional[str]] = mapped_column(String(50))
    connection_degree: Mapped[Optional[str]] = mapped_column(String(20))

    matched_keyword: Mapped[Optional[str]] = mapped_column(String(100))
    role_bucket: Mapped[Optional[str]] = mapped_column(String(50))
    search_confidence: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    current_title: Mapped[Optional[str]] = mapped_column(String(500))
    current_company: Mapped[Optional[str]] = mapped_column(String(500))
    tenure_hint: Mapped[Optional[str]] = mapped_column(String(100))
    about_text: Mapped[Optional[str]] = mapped_column(Text)
    profile_summary_text: Mapped[Optional[str]] = mapped_column(Text)
    experiences_json: Mapped[Optional[str]] = mapped_column(Text)
    recent_posts_json: Mapped[Optional[str]] = mapped_column(Text)
    llm_assessment_json: Mapped[Optional[str]] = mapped_column(Text)
    contact_info_available: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    message_available: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    connect_available: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    company_match_confidence: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    outreach_feasibility_score: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    contact_relevance_score: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    contact_relevance_bucket: Mapped[Optional[str]] = mapped_column(String(50))

    assessment_reasons_json: Mapped[Optional[str]] = mapped_column(Text)
    assessment_warnings_json: Mapped[Optional[str]] = mapped_column(Text)

    dossier_status: Mapped[str] = mapped_column(String(50), nullable=False, default="pending")
    dossier_json: Mapped[Optional[str]] = mapped_column(Text)
    outreach_message: Mapped[Optional[str]] = mapped_column(Text)
    outreach_generated_at_utc: Mapped[Optional[datetime]] = mapped_column(DateTime)
    outreach_dispatch_status: Mapped[str] = mapped_column(
        String(50), nullable=False, default="not_sent"
    )
    outreach_dispatch_channel: Mapped[Optional[str]] = mapped_column(String(50))
    outreach_dispatch_target: Mapped[Optional[str]] = mapped_column(String(500))
    outreach_dispatch_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    outreach_dispatch_error: Mapped[Optional[str]] = mapped_column(Text)
    outreach_last_dispatch_at_utc: Mapped[Optional[datetime]] = mapped_column(DateTime)
    outreach_sent:            Mapped[bool]           = mapped_column(Boolean,      nullable=False, default=False)
    outreach_status:          Mapped[Optional[str]]  = mapped_column(String(50))
    outreach_type:            Mapped[Optional[str]]  = mapped_column(String(50))
    outreach_error:           Mapped[Optional[str]]  = mapped_column(Text)
    outreach_ts:              Mapped[Optional[datetime]] = mapped_column(DateTime)
    outreach_attempts:        Mapped[int]            = mapped_column(Integer,      nullable=False, default=0)
    outreach_last_attempt_ts: Mapped[Optional[datetime]] = mapped_column(DateTime)
    
    outreach_sent_at_utc: Mapped[Optional[datetime]] = mapped_column(DateTime)

    outreach_required:        Mapped[bool]           = mapped_column(Boolean,      nullable=False, default=False)
    outreach_sent:            Mapped[bool]           = mapped_column(Boolean,      nullable=False, default=False)
    outreach_status:          Mapped[Optional[str]]  = mapped_column(String(50))
    outreach_type:            Mapped[Optional[str]]  = mapped_column(String(50))
    outreach_error:           Mapped[Optional[str]]  = mapped_column(Text)
    outreach_ts:              Mapped[Optional[datetime]] = mapped_column(DateTime)
    outreach_attempts:        Mapped[int]            = mapped_column(Integer,      nullable=False, default=0)
    outreach_last_attempt_ts: Mapped[Optional[datetime]] = mapped_column(DateTime)
    outreach_in_progress:     Mapped[bool]           = mapped_column(Boolean,      nullable=False, default=False)

    assessed_at_utc: Mapped[Optional[datetime]] = mapped_column(DateTime)
    created_at_utc: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=lambda: datetime.now(timezone.utc)
    )

    @property
    def assessment_reasons(self) -> list[str]:
        if not self.assessment_reasons_json:
            return []
        try:
            return json.loads(self.assessment_reasons_json)
        except Exception:
            return []

    @property
    def assessment_warnings(self) -> list[str]:
        if not self.assessment_warnings_json:
            return []
        try:
            return json.loads(self.assessment_warnings_json)
        except Exception:
            return []

    @property
    def experiences(self) -> list[dict]:
        if not self.experiences_json:
            return []
        try:
            parsed = json.loads(self.experiences_json)
            return parsed if isinstance(parsed, list) else []
        except Exception:
            return []

    @property
    def recent_posts(self) -> list[str]:
        if not self.recent_posts_json:
            return []
        try:
            parsed = json.loads(self.recent_posts_json)
            return parsed if isinstance(parsed, list) else []
        except Exception:
            return []


# ---------------------------------------------------------------------------
# Search keyword configuration table
# ---------------------------------------------------------------------------

class SearchKeywordORM(Base):
    __tablename__ = "search_keywords"
    __table_args__ = (
        UniqueConstraint("keyword", "location", name="uq_search_keyword_location"),
        Index("ix_search_keywords_active", "active"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    keyword: Mapped[str] = mapped_column(String(255), nullable=False)
    location: Mapped[Optional[str]] = mapped_column(String(255))
    max_job_age_days: Mapped[int] = mapped_column(Integer, nullable=False, default=7)
    max_jobs: Mapped[int] = mapped_column(Integer, nullable=False, default=50)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_run_utc: Mapped[Optional[datetime]] = mapped_column(DateTime)
    created_at_utc: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    updated_at_utc: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=lambda: datetime.now(timezone.utc)
    )
# ---------------------------------------------------------------------------
# LinkedIn credential store
# ---------------------------------------------------------------------------

class LinkedInCredentialORM(Base):
    __tablename__ = "linkedin_credentials"
    __table_args__ = (
        UniqueConstraint("email", name="uq_linkedin_credentials_email"),
        Index("ix_linkedin_credentials_active_priority", "active", "priority"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    password: Mapped[str] = mapped_column(String(1000), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=100)

    last_login_attempt_utc: Mapped[Optional[datetime]] = mapped_column(DateTime)
    last_login_success_utc: Mapped[Optional[datetime]] = mapped_column(DateTime)
    last_login_failure_reason: Mapped[Optional[str]] = mapped_column(Text)

    created_at_utc: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    updated_at_utc: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=lambda: datetime.now(timezone.utc)
    )


# ---------------------------------------------------------------------------
# Candidate hunting table
# ---------------------------------------------------------------------------

class CandidateProfileORM(Base):
    __tablename__ = "candidate_profiles"
    __table_args__ = (
        UniqueConstraint("agent_id", "job_id", "linkedin_profile_url", name="uq_candidate_profile_agent_job"),
        Index("ix_candidate_profile_agent_id", "agent_id"),
        Index("ix_candidate_profile_status", "profile_status"),
        Index("ix_candidate_profile_job_id", "job_id"),
        Index("ix_candidate_profile_run_id", "search_run_id"),
        Index("ix_candidate_profile_relevance", "jd_relevance_score"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    agent_id: Mapped[Optional[int]] = mapped_column(Integer)
    job_id: Mapped[int] = mapped_column(Integer, nullable=False)
    search_run_id: Mapped[str] = mapped_column(String(64), nullable=False)
    search_query: Mapped[Optional[str]] = mapped_column(String(500))

    linkedin_profile_url: Mapped[str] = mapped_column(String(500), nullable=False)
    linkedin_public_id: Mapped[Optional[str]] = mapped_column(String(255))
    full_name: Mapped[Optional[str]] = mapped_column(String(255))
    headline: Mapped[Optional[str]] = mapped_column(String(500))
    location_text: Mapped[Optional[str]] = mapped_column(String(255))
    current_summary_text: Mapped[Optional[str]] = mapped_column(String(500))
    connection_degree: Mapped[Optional[str]] = mapped_column(String(20))
    is_open_to_work: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    search_page_no: Mapped[Optional[int]] = mapped_column(Integer)
    position_on_page: Mapped[Optional[int]] = mapped_column(Integer)
    source_search_url: Mapped[Optional[str]] = mapped_column(String(1200))
    discovered_at_utc: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=lambda: datetime.now(timezone.utc)
    )

    extraction_stage: Mapped[str] = mapped_column(String(50), nullable=False, default="search_ingestion")
    profile_status: Mapped[str] = mapped_column(String(50), nullable=False, default="queued")
    stage_status_json: Mapped[Optional[str]] = mapped_column(Text)
    stage_errors_json: Mapped[Optional[str]] = mapped_column(Text)

    profile_name: Mapped[Optional[str]] = mapped_column(String(255))
    profile_headline: Mapped[Optional[str]] = mapped_column(String(500))
    profile_location: Mapped[Optional[str]] = mapped_column(String(255))
    profile_about_text: Mapped[Optional[str]] = mapped_column(Text)
    current_title: Mapped[Optional[str]] = mapped_column(String(500))
    current_company: Mapped[Optional[str]] = mapped_column(String(500))
    experiences_json: Mapped[Optional[str]] = mapped_column(Text)
    education_json: Mapped[Optional[str]] = mapped_column(Text)
    skills_json: Mapped[Optional[str]] = mapped_column(Text)
    certifications_json: Mapped[Optional[str]] = mapped_column(Text)
    activity_json: Mapped[Optional[str]] = mapped_column(Text)
    contact_points_json: Mapped[Optional[str]] = mapped_column(Text)
    resume_urls_json: Mapped[Optional[str]] = mapped_column(Text)
    resume_text: Mapped[Optional[str]] = mapped_column(Text)

    job_seeking_status: Mapped[Optional[str]] = mapped_column(String(50))
    job_seeking_score: Mapped[Optional[int]] = mapped_column(Integer)
    confidence_score: Mapped[Optional[int]] = mapped_column(Integer)
    top_evidence_json: Mapped[Optional[str]] = mapped_column(Text)
    negative_evidence_json: Mapped[Optional[str]] = mapped_column(Text)
    ambiguity_notes_json: Mapped[Optional[str]] = mapped_column(Text)

    jd_relevance_score: Mapped[Optional[int]] = mapped_column(Integer)
    jd_dimension_scores_json: Mapped[Optional[str]] = mapped_column(Text)
    missing_critical_requirements_json: Mapped[Optional[str]] = mapped_column(Text)
    llm_summary_text: Mapped[Optional[str]] = mapped_column(Text)
    llm_payload_json: Mapped[Optional[str]] = mapped_column(Text)

    profile_extracted_at_utc: Mapped[Optional[datetime]] = mapped_column(DateTime)
    scored_at_utc: Mapped[Optional[datetime]] = mapped_column(DateTime)
    completed_at_utc: Mapped[Optional[datetime]] = mapped_column(DateTime)

    failure_reason: Mapped[Optional[str]] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at_utc: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    updated_at_utc: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=lambda: datetime.now(timezone.utc)
    )

    @property
    def stage_status(self) -> dict:
        if not self.stage_status_json:
            return {}
        try:
            parsed = json.loads(self.stage_status_json)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}


# ---------------------------------------------------------------------------
# App settings table (runtime-configurable, non-secret settings)
# ---------------------------------------------------------------------------

class AppSettingsORM(Base):
    __tablename__ = "app_settings"

    setting_key: Mapped[str] = mapped_column(String(100), primary_key=True)
    setting_value: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(String(1000))
    config_type: Mapped[Optional[str]] = mapped_column(String(100))
    updated_at_utc: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    updated_by: Mapped[Optional[str]] = mapped_column(String(100))


# ---------------------------------------------------------------------------
# DB engine factory + session
# ---------------------------------------------------------------------------

_engine = None
_SessionFactory = None

# All tables that must exist before the application starts.
# If any are missing, init_db.py has not been run.
_REQUIRED_TABLES = frozenset({
    "agent_definitions",
    "agent_profiles",
    "agent_keywords",
    "agent_runs",
    "agent_tools",
    "app_settings",
    "naukri_jobs",
    "company_research",
    "prospects",
    "search_keywords",
    "linkedin_credentials",
    "candidate_profiles",
    "ui_users",
    "pipeline_runs",
    "linkedin_conversations",
    "ingestion_runs",
})


def _verify_schema(engine) -> None:
    """
    Confirm that every required table exists in the live database.

    Raises RuntimeError with an actionable message if any table is missing.
    This is the ONLY schema interaction the application performs at startup —
    it never creates or alters tables.

    To create the schema run:  python init_db.py
    """
    dialect = engine.dialect.name
    try:
        with engine.connect() as conn:
            if dialect == "mssql":
                rows = conn.execute(
                    text(
                        "SELECT TABLE_NAME FROM INFORMATION_SCHEMA.TABLES "
                        "WHERE TABLE_TYPE = 'BASE TABLE'"
                    )
                ).fetchall()
            else:
                rows = conn.execute(
                    text(
                        "SELECT table_name FROM information_schema.tables "
                        "WHERE table_schema = DATABASE()"
                    )
                ).fetchall()
    except OperationalError as exc:
        raise RuntimeError(
            f"Cannot connect to the database: {exc}\n"
            "Ensure the database server is running and DB_URL is set correctly.\n"
            "If the database has not been initialised, run:  python init_db.py"
        ) from exc

    existing = {str(row[0]).lower() for row in rows}
    missing = sorted(t for t in _REQUIRED_TABLES if t.lower() not in existing)

    if missing:
        raise RuntimeError(
            f"Database schema is incomplete. Missing tables: {missing}\n"
            "The database must be initialised before starting the application.\n"
            "Run:  python init_db.py"
        )

    logger.info("Schema verification passed — all %d required tables present.", len(_REQUIRED_TABLES))


def init_db(config: DatabaseConfig) -> None:
    """
    Connect to an already-initialised database and verify the schema.

    CONTRACT:
      - This function NEVER creates tables, alters schema, or inserts seed data.
      - It raises RuntimeError immediately if the schema is missing or incomplete.
      - All one-time setup is performed exclusively by  python init_db.py

    Raises:
        RuntimeError: If required tables are missing or the DB is unreachable.
    """
    global _engine, _SessionFactory

    _engine = create_engine(
        config.sqlalchemy_url,
        echo=config.echo_sql,
        pool_size=config.pool_size,
        pool_recycle=config.pool_recycle_seconds,
        pool_pre_ping=True,
        future=True,
    )

    _verify_schema(_engine)
    reset_runtime_settings_cache()

    _SessionFactory = sessionmaker(bind=_engine, expire_on_commit=False)
    logger.info("Database connection established and schema verified.")


def get_engine():
    if _engine is None:
        raise RuntimeError(
            "Database not initialised. Call init_db() first.\n"
            "If the database schema has not been created, run:  python init_db.py"
        )
    return _engine


@contextmanager
def session_scope() -> Generator[Session, None, None]:
    if _SessionFactory is None:
        raise RuntimeError(
            "Database not initialised. Call init_db() first.\n"
            "If the database schema has not been created, run:  python init_db.py"
        )
    session: Session = _SessionFactory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def resolve_agent_id(
    *,
    agent_id: Optional[int] = None,
    agent_key: Optional[str] = None,
    default_agent_key: Optional[str] = None,
) -> int:
    if agent_id is not None:
        with session_scope() as session:
            row = session.query(AgentDefinitionORM.id).filter_by(id=int(agent_id)).one_or_none()
            if row is not None:
                return int(agent_id)

    key = (agent_key or "").strip()
    if key:
        with session_scope() as session:
            row = session.query(AgentDefinitionORM.id).filter_by(agent_key=key).one_or_none()
            if row is not None:
                return int(row[0])

    fallback_key = (
        default_agent_key
        or os.getenv("AGENT_DEFAULT_KEY", "default-staffing")
        or "staffing-agent"
    ).strip()
    with session_scope() as session:
        row = session.query(AgentDefinitionORM.id).filter_by(agent_key=fallback_key).one_or_none()
        if row is not None:
            return int(row[0])

    raise RuntimeError(
        f"No agent found for key='{fallback_key}'. "
        "Ensure init_db.py has been run and the default agent was seeded."
    )


def get_agent_prompt_context(agent_id: int) -> Dict[str, Any]:
    """
    Return prompt-context dictionary built from current agent profile fields.
    Includes keywords_by_type so callers can inject them into prompts.
    """
    log = logging.getLogger("prospect.db.prompt_context")
    with session_scope() as session:
        profile = (
            session.query(AgentProfileORM)
            .filter_by(agent_id=int(agent_id), is_current=True)
            .order_by(AgentProfileORM.version.desc(), AgentProfileORM.id.desc())
            .first()
        )
        agent = session.query(AgentDefinitionORM).filter_by(id=int(agent_id)).one_or_none()

        keywords = (
            session.query(AgentKeywordORM)
            .filter_by(agent_id=int(agent_id), active=True)
            .order_by(AgentKeywordORM.keyword_type.asc(), AgentKeywordORM.weight.desc())
            .all()
        )

        if not profile:
            log.warning("No current profile found for agent_id=%s. Using empty context.", agent_id)
            return {}

        context: Dict[str, Any] = {}
        if profile.prompt_profile_json:
            try:
                parsed = json.loads(profile.prompt_profile_json)
                if isinstance(parsed, dict):
                    context.update(parsed)
            except Exception:
                log.warning("Could not parse prompt_profile_json for agent_id=%s.", agent_id)

        for field_key, field_val in [
            ("persona_title", profile.persona_title),
            ("domain_focus", profile.domain_focus),
            ("service_offering", profile.service_offering),
            ("sales_objective", profile.sales_objective),
        ]:
            if field_val:
                context[field_key] = field_val

        if profile.target_buyer_roles_json:
            try:
                roles = json.loads(profile.target_buyer_roles_json)
                if isinstance(roles, list) and roles:
                    context["target_buyer_roles"] = ", ".join(str(r) for r in roles)
            except Exception:
                pass

        if profile.value_outcomes_json:
            try:
                outcomes = json.loads(profile.value_outcomes_json)
                if isinstance(outcomes, list) and outcomes:
                    context["value_outcomes"] = ", ".join(str(o) for o in outcomes)
            except Exception:
                pass

        if agent:
            context["agent_name"] = agent.name or ""
            context["agent_type"] = agent.agent_type or ""
            context["agent_key"] = agent.agent_key or ""

        keywords_by_type: Dict[str, List[str]] = {}
        for kw in keywords:
            ktype = (kw.keyword_type or "general").strip().lower()
            keywords_by_type.setdefault(ktype, [])
            keywords_by_type[ktype].append(kw.keyword)

        if keywords_by_type:
            context["keywords_by_type"] = keywords_by_type
            all_keywords = [kw for kws in keywords_by_type.values() for kw in kws]
            if all_keywords:
                context["active_keywords"] = ", ".join(all_keywords)

        log.debug(
            "Resolved prompt context for agent_id=%s: persona='%s', domain='%s', keywords=%d",
            agent_id,
            context.get("persona_title", ""),
            context.get("domain_focus", ""),
            sum(len(v) for v in keywords_by_type.values()),
        )
        return context


def seed_default_keywords(config_keywords: list[str], location: Optional[str]) -> None:
    """Seed SearchKeywordORM from config if the table is empty."""
    with session_scope() as session:
        if session.query(SearchKeywordORM).count() > 0:
            return
        for kw in config_keywords:
            session.add(SearchKeywordORM(keyword=kw, location=location))
        logger.info("Seeded %d default search keywords.", len(config_keywords))

# ---------------------------------------------------------------------------
# LinkedIn conversation threading table
# ---------------------------------------------------------------------------

class LinkedInConversationORM(Base):
    """
    One row per prospect.  Tracks the full back-and-forth message thread
    and the current lead stage for automated reply handling.
    """
    __tablename__ = "linkedin_conversations"
    __table_args__ = (
        UniqueConstraint("prospect_id", name="uq_conv_prospect"),
        Index("ix_conv_agent",        "agent_id"),
        Index("ix_conv_status",       "conversation_status"),
        Index("ix_conv_last_checked", "last_checked_utc"),
        Index("ix_conv_lead_stage",   "lead_stage"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    prospect_id:          Mapped[int]           = mapped_column(Integer,      nullable=False)
    agent_id:             Mapped[Optional[int]] = mapped_column(Integer)
    linkedin_profile_url: Mapped[str]           = mapped_column(String(500),  nullable=False)

    # State machine
    # active | closed | not_interested | meeting_booked
    conversation_status:  Mapped[str]           = mapped_column(String(50),   nullable=False, default="active")

    # Lead qualification
    # cold | warming | interested | hot | converted | dead
    lead_stage:           Mapped[str]           = mapped_column(String(50),   nullable=False, default="cold")

    # Full thread: JSON list of {"role":"us"|"them","text":"...","ts":"ISO8601"}
    thread_json:          Mapped[Optional[str]] = mapped_column(Text)

    messages_sent:        Mapped[int]           = mapped_column(Integer, nullable=False, default=0)
    messages_received:    Mapped[int]           = mapped_column(Integer, nullable=False, default=0)

    first_message_sent_utc:  Mapped[Optional[datetime]] = mapped_column(DateTime)
    last_message_sent_utc:   Mapped[Optional[datetime]] = mapped_column(DateTime)
    last_reply_received_utc: Mapped[Optional[datetime]] = mapped_column(DateTime)
    last_checked_utc:        Mapped[Optional[datetime]] = mapped_column(DateTime)

    # DEPRECATED — dead columns, quarantined (audit fix F). Nothing in the
    # codebase reads or writes these; they exist only because the live MySQL
    # table has them (sql/add_conversations.sql). Do not use for new work —
    # removing them is a schema change (models + init_db + manual migration).
    pending_reply_text:          Mapped[Optional[str]]      = mapped_column(Text)
    pending_reply_generated_utc: Mapped[Optional[datetime]] = mapped_column(DateTime)

    # Human handoff — set when the bot detects real interest, hits its reply cap,
    # or the classifier defaults to handoff on error. Once set, conversation_status
    # is also flipped to 'handed_off' so _load_active_conversations (status=='active')
    # naturally excludes the row from all future polls.
    handed_off_at_utc:  Mapped[Optional[datetime]] = mapped_column(DateTime)
    handoff_reason:      Mapped[Optional[str]]      = mapped_column(String(255))
    handoff_email_sent:  Mapped[bool]               = mapped_column(Boolean, nullable=False, default=False)

    # Reply-cadence bookkeeping
    bot_reply_count:          Mapped[int]           = mapped_column(Integer, nullable=False, default=0)
    answered_question_count:  Mapped[int]           = mapped_column(Integer, nullable=False, default=0)
    # live | batch — whether the prospect appears to be actively engaged right now
    conversation_mode:        Mapped[str]            = mapped_column(String(20), nullable=False, default="batch")
    last_live_detected_utc:   Mapped[Optional[datetime]] = mapped_column(DateTime)

    last_error:  Mapped[Optional[str]] = mapped_column(Text)
    error_count: Mapped[int]           = mapped_column(Integer, nullable=False, default=0)

    created_at_utc: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    updated_at_utc: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=lambda: datetime.now(timezone.utc)
    )

    # ------------------------------------------------------------------
    # Convenience helpers
    # ------------------------------------------------------------------

    def get_thread(self) -> list[dict]:
        """Return the message thread as a list of dicts."""
        if not self.thread_json:
            return []
        try:
            parsed = json.loads(self.thread_json)
            return parsed if isinstance(parsed, list) else []
        except Exception:
            return []

    def append_message(self, role: str, text: str) -> None:
        """
        Append one message to thread_json in-place.
        role must be 'us' or 'them'.
        Call session.commit() afterwards to persist.
        """
        thread = self.get_thread()
        thread.append({
            "role": role,
            "text": text,
            "ts":   datetime.now(timezone.utc).isoformat(),
        })
        self.thread_json = json.dumps(thread)

    def count_our_messages_since(self, cutoff_utc: datetime) -> int:
        """
        Count 'us' messages in thread_json with a parseable ts at/after cutoff_utc.

        Messages flagged "live": true (sent inside one real-time live session)
        are excluded — the 24h cap governs batch replies, and ending a live chat
        mid-conversation because of it would look worse than the extra messages.
        """
        count = 0
        for msg in self.get_thread():
            if msg.get("role") != "us":
                continue
            if msg.get("live"):
                continue
            ts_raw = msg.get("ts")
            if not ts_raw:
                continue
            try:
                ts = datetime.fromisoformat(str(ts_raw).replace("Z", "+00:00"))
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
            except Exception:
                continue
            if ts >= cutoff_utc:
                count += 1
        return count


# ---------------------------------------------------------------------------
# RAG website ingestion run history
# ---------------------------------------------------------------------------

class IngestionRunORM(Base):
    """One row per website-ingestion (crawl -> chunk -> embed -> store) run."""
    __tablename__ = "ingestion_runs"
    __table_args__ = (
        Index("ix_ingestion_runs_started_at", "started_at_utc"),
        Index("ix_ingestion_runs_status", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    source:     Mapped[str]           = mapped_column(String(100), nullable=False, default="website")
    target_url: Mapped[Optional[str]] = mapped_column(String(500))

    # running | completed | failed
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="running")
    # crawling | embedding
    stage:  Mapped[Optional[str]] = mapped_column(String(30))

    pages_crawled:   Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    pages_in_queue:  Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    chunks_created:  Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    chunks_embedded: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    chunks_stored:   Mapped[Optional[int]] = mapped_column(Integer)

    progress_pct: Mapped[float]           = mapped_column(Float, nullable=False, default=0.0)
    eta_seconds:  Mapped[Optional[int]]   = mapped_column(Integer)
    error_text:   Mapped[Optional[str]]   = mapped_column(Text)
    triggered_by: Mapped[Optional[str]]   = mapped_column(String(100))

    started_at_utc: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    ended_at_utc: Mapped[Optional[datetime]] = mapped_column(DateTime)
