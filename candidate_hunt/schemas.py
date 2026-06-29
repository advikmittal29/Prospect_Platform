from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional


@dataclass
class JobContext:
    job_id: int
    title: Optional[str]
    company_name: Optional[str]
    location_text: Optional[str]
    experience_text: Optional[str]
    industry: Optional[str]
    role: Optional[str]
    role_category: Optional[str]
    job_description_text: Optional[str]
    skills: List[str] = field(default_factory=list)


@dataclass
class SearchQueryPlan:
    role_keywords: List[str] = field(default_factory=list)
    alternate_titles: List[str] = field(default_factory=list)
    seniority_variants: List[str] = field(default_factory=list)
    skill_phrases: List[str] = field(default_factory=list)
    negative_keywords: List[str] = field(default_factory=list)
    final_queries: List[str] = field(default_factory=list)
    llm_used: bool = False


@dataclass
class CandidateSearchCard:
    linkedin_profile_url: str
    linkedin_public_id: Optional[str]
    full_name: Optional[str]
    headline: Optional[str]
    location_text: Optional[str]
    current_summary_text: Optional[str]
    connection_degree: Optional[str]
    is_open_to_work: bool
    search_page_no: int
    position_on_page: int
    source_search_url: str


@dataclass
class CandidateProfileIntelligence:
    profile_name: Optional[str]
    profile_headline: Optional[str]
    profile_location: Optional[str]
    profile_about_text: Optional[str]
    current_title: Optional[str]
    current_company: Optional[str]

    experiences: List[Dict[str, Any]] = field(default_factory=list)
    education: List[Dict[str, Any]] = field(default_factory=list)
    skills: List[str] = field(default_factory=list)
    certifications: List[Dict[str, Any]] = field(default_factory=list)
    activity: List[Dict[str, Any]] = field(default_factory=list)
    contact_points: List[Dict[str, Any]] = field(default_factory=list)
    resume_urls: List[str] = field(default_factory=list)
    resume_text: Optional[str] = None

    extraction_stage: str = "profile_extraction"
    extracted_at_utc: Optional[datetime] = None


@dataclass
class CandidateAssessment:
    job_seeking_status: str
    job_seeking_score: int
    confidence_score: int
    top_evidence: List[str] = field(default_factory=list)
    negative_evidence: List[str] = field(default_factory=list)
    ambiguity_notes: List[str] = field(default_factory=list)

    jd_relevance_score: int = 0
    jd_dimension_scores: Dict[str, int] = field(default_factory=dict)
    missing_critical_requirements: List[str] = field(default_factory=list)

    llm_summary_text: Optional[str] = None
    llm_payload: Optional[Dict[str, Any]] = None
