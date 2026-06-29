export type PipelineType = "ingest" | "research" | "intelligence" | "candidate_hunt";

export interface DashboardSummary {
  metrics: Record<string, number>;
  recent_runs: PipelineRun[];
  agent_id?: number | null;
  requested_by: string;
}

export interface PipelineRun {
  id: number;
  pipeline: PipelineType;
  agent_id?: number | null;
  status: string;
  started_at_utc?: string;
  ended_at_utc?: string;
  triggered_by?: string;
  message?: string;
  log_text?: string;
}

export interface JobRow {
  id: number;
  agent_id?: number | null;
  source?: string;
  search_keyword?: string;
  search_location?: string;
  title?: string;
  company_name?: string;
  posted_date?: string;
  posted_relative?: string;
  experience_text?: string;
  salary_text?: string;
  location_text?: string;
  employment_type?: string;
  industry?: string;
  department?: string;
  role?: string;
  role_category?: string;
  education?: string;
  extraction_confidence?: number;
  researched?: boolean;
  fetched_at_utc?: string;
  job_url?: string;
  canonical_job_url?: string;
}

export interface CompanyRow {
  id: number;
  agent_id?: number | null;
  company_name: string;
  linkedin_url?: string;
  linkedin_match_confidence?: number;
  tagline?: string;
  industry?: string;
  location?: string;
  employee_range?: string;
  followers?: string;
  research_status?: string;
  updated_at_utc?: string;
  prospect_count?: number;
}

export interface ProspectRow {
  id: number;
  agent_id?: number | null;
  company_research_id?: number;
  company_name?: string;
  name?: string;
  headline?: string;
  current_title?: string;
  current_company?: string;
  linkedin_profile_url?: string;
  role_bucket?: string;
  company_match_confidence?: number;
  outreach_feasibility_score?: number;
  contact_relevance_score?: number;
  contact_relevance_bucket?: string;
  profile_summary_text?: string;
  dossier_status?: string;
  outreach_generated_at_utc?: string;
  outreach_dispatch_status?: string;
  outreach_dispatch_channel?: string;
  outreach_dispatch_attempts?: number;
  outreach_sent_at_utc?: string;
  assessed_at_utc?: string;
}

export interface ProspectDetail extends ProspectRow {
  about_text?: string;
  tenure_hint?: string;
  assessment_reasons_json?: string;
  assessment_warnings_json?: string;
  llm_assessment_json?: string;
  experiences_json?: string;
  recent_posts_json?: string;
  dossier_json?: string;
  outreach_message?: string;
  experiences?: Array<Record<string, unknown>>;
  recent_posts?: string[];
  llm_assessment?: Record<string, unknown>;
  dossier?: Record<string, unknown>;
  outreach_message_json?: Record<string, unknown> | string;
}

export interface CandidateProfileRow {
  id: number;
  agent_id?: number | null;
  job_id: number;
  search_run_id?: string;
  full_name?: string;
  headline?: string;
  location_text?: string;
  current_title?: string;
  current_company?: string;
  profile_status?: string;
  job_seeking_status?: string;
  job_seeking_score?: number;
  jd_relevance_score?: number;
  is_open_to_work?: boolean;
  linkedin_profile_url?: string;
  updated_at_utc?: string;
  job_title?: string;
  job_company?: string;
}

export interface CandidateProfileDetail extends CandidateProfileRow {
  stage_status_json?: unknown;
  stage_errors_json?: unknown;
  experiences_json?: unknown;
  education_json?: unknown;
  skills_json?: unknown;
  certifications_json?: unknown;
  activity_json?: unknown;
  contact_points_json?: unknown;
  resume_urls_json?: unknown;
  top_evidence_json?: unknown;
  negative_evidence_json?: unknown;
  ambiguity_notes_json?: unknown;
  jd_dimension_scores_json?: unknown;
  missing_critical_requirements_json?: unknown;
  llm_payload_json?: unknown;
  llm_summary_text?: string;
  resume_text?: string;
  failure_reason?: string;
}

export interface LinkedInCredentialRow {
  id: number;
  email: string;
  active: boolean;
  priority: number;
  last_login_attempt_utc?: string;
  last_login_success_utc?: string;
  last_login_failure_reason?: string;
}

export interface SettingRow {
  setting_key: string;
  setting_value: unknown;
  description?: string;
  config_type?: string;
  updated_at_utc?: string;
  updated_by?: string;
  source?: string;
}

export interface AgentDefinitionRow {
  id: number;
  agent_key: string;
  name: string;
  description?: string;
  status: "active" | "paused" | "archived" | string;
  agent_type?: string;
  created_at_utc?: string;
  updated_at_utc?: string;
  current_profile_version?: number | null;
  persona_title?: string | null;
  service_offering?: string | null;
}

export interface AgentProfilePayload {
  id?: number;
  version?: number;
  persona_title?: string | null;
  domain_focus?: string | null;
  service_offering?: string | null;
  target_buyer_roles?: string[] | null;
  sales_objective?: string | null;
  value_outcomes?: string[] | null;
  icp_rules?: Record<string, unknown> | null;
  targeting_policy?: Record<string, unknown> | null;
  pipeline_policy?: Record<string, unknown> | null;
  channel_policy?: Record<string, unknown> | null;
  prompt_profile?: Record<string, unknown> | null;
  runtime_policy?: Record<string, unknown> | null;
  created_at_utc?: string;
  created_by?: string;
}

export interface AgentProfileResponse {
  agent: {
    id: number;
    agent_key: string;
    name: string;
    status: string;
    agent_type?: string;
  };
  profile: AgentProfilePayload;
}

export interface AgentKeywordRow {
  id: number;
  agent_id: number;
  keyword_type: string;
  keyword: string;
  weight: number;
  active: boolean;
  updated_at_utc?: string;
}

// ─── Paginated response wrapper ────────────────────────────────────────
export interface PagedResponse<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
}

export type PagedJobs        = PagedResponse<JobRow>;
export type PagedCompanies   = PagedResponse<CompanyRow>;
export type PagedProspects   = PagedResponse<ProspectRow>;
export type PagedRuns        = PagedResponse<PipelineRun>;
export type PagedKeywords    = PagedResponse<KeywordRow>;
export type PagedCandidates  = PagedResponse<CandidateProfileRow>;

// ─── Ingestion config record ────────────────────────────────────────────
export interface KeywordRow {
  id: number;
  keyword: string;
  location?: string | null;
  max_job_age_days: number;
  max_jobs: number;
  active: boolean;
  last_run_utc?: string | null;
  created_at_utc?: string | null;
  updated_at_utc?: string | null;
}