import type {
  AgentDefinitionRow, AgentKeywordRow, AgentProfilePayload, AgentProfileResponse,
  CompanyRow, DashboardSummary, KeywordRow, LinkedInCredentialRow,
  PagedCandidates,
  PagedCompanies,
  PagedJobs,
  PagedProspects,
  PagedRuns,
  PipelineRun, PipelineType, ProspectDetail, ProspectRow, SettingRow,
} from "./types";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "http://127.0.0.1:8000";
const TOKEN_KEY = "prospect_ui_token";
const USER_KEY  = "prospect_ui_user";

export function getToken(): string | null    { return localStorage.getItem(TOKEN_KEY); }
export function getUsername(): string | null { return localStorage.getItem(USER_KEY); }

export function clearSession(): void {
  localStorage.removeItem(TOKEN_KEY);
  localStorage.removeItem(USER_KEY);
}

/**
 * Redirect to the login page, clearing the session first.
 * Uses window.location.replace so the protected page is not added to history
 * (pressing Back won't re-enter the protected area).
 */
function redirectToLogin(): void {
  clearSession();
  // Only redirect if we are not already on the login page to avoid redirect loops.
  if (!window.location.pathname.startsWith("/login")) {
    window.location.replace("/login");
  }
}

export async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const token = getToken();
  const headers: HeadersInit = {
    "Content-Type": "application/json",
    ...(init?.headers ?? {}),
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
  };

  let res: Response;
  try {
    res = await fetch(`${API_BASE_URL}${path}`, { ...init, headers });
  } catch (networkError) {
    // Network failures (server unreachable, CORS, etc.) are surfaced as-is.
    throw new Error(`Network error: ${networkError}`);
  }

  if (res.status === 401) {
    // Session has expired or the token is invalid.
    // Clear local storage and force a hard redirect to /login.
    redirectToLogin();
    // Throw so any in-flight callers can clean up their loading state.
    throw new Error("Session expired – redirecting to login.");
  }

  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(text || `Request failed with ${res.status}`);
  }

  return (await res.json()) as T;
}

// ─── Auth ─────────────────────────────────────────────────────────────
export async function login(username: string, password: string): Promise<void> {
  const r = await request<{ token: string; username: string }>("/api/auth/login", {
    method: "POST", body: JSON.stringify({ username, password }),
  });
  localStorage.setItem(TOKEN_KEY, r.token);
  localStorage.setItem(USER_KEY,  r.username);
}

export function logout(): void {
  redirectToLogin();
}

const AGENT_SCOPE_KEY = "prospect_active_agent_id";
export const AGENT_SCOPE_EVENT = "agent_scope_change";

export function getActiveAgentId(): number | null {
  const v = localStorage.getItem(AGENT_SCOPE_KEY);
  return v ? Number(v) : null;
}

export function setActiveAgentId(id: number | null): void {
  if (id == null) localStorage.removeItem(AGENT_SCOPE_KEY);
  else localStorage.setItem(AGENT_SCOPE_KEY, String(id));
  window.dispatchEvent(new CustomEvent(AGENT_SCOPE_EVENT, { detail: id }));
}

// ─── Agents ───────────────────────────────────────────────────────────
export async function getAgents(): Promise<{ items: AgentDefinitionRow[]; count: number }> {
  return request("/api/agents");
}

export async function getAgent(agentId: number): Promise<AgentDefinitionRow> {
  const r = await request<{ items: AgentDefinitionRow[] }>("/api/agents");
  const agent = r.items.find((a) => a.id === agentId);
  if (!agent) throw new Error(`Agent ${agentId} not found`);
  return agent;
}

export async function createAgent(payload: {
  agent_key: string; name: string; description?: string;
  agent_type?: string; status?: "active" | "paused" | "archived";
}): Promise<{ id: number; agent_key: string }> {
  return request("/api/agents", { method: "POST", body: JSON.stringify(payload) });
}

export async function updateAgent(
  agentId: number,
  payload: { name?: string; description?: string; status?: string }
): Promise<{ ok: boolean }> {
  return request(`/api/agents/${agentId}`, { method: "PUT", body: JSON.stringify(payload) });
}

export async function getAgentProfile(agentId: number): Promise<AgentProfileResponse> {
  return request(`/api/agents/${agentId}/profile`);
}

export async function updateAgentProfile(
  agentId: number,
  payload: AgentProfilePayload
): Promise<{ ok: boolean; profile_id: number; version: number }> {
  return request(`/api/agents/${agentId}/profile`, { method: "PUT", body: JSON.stringify(payload) });
}

export async function getAgentKeywords(agentId: number): Promise<{ items: AgentKeywordRow[]; count: number }> {
  return request(`/api/agents/${agentId}/keywords`);
}

export async function upsertAgentKeyword(
  agentId: number,
  payload: { keyword_type: string; keyword: string; weight: number; active: boolean }
): Promise<{ id: number }> {
  return request(`/api/agents/${agentId}/keywords`, { method: "POST", body: JSON.stringify(payload) });
}

export async function deleteAgentKeyword(agentId: number, keywordId: number): Promise<{ ok: boolean }> {
  return request(`/api/agents/${agentId}/keywords/${keywordId}`, { method: "DELETE" });
}

// ─── Dashboard ────────────────────────────────────────────────────────
export async function getDashboardSummary(agentId?: number | null): Promise<DashboardSummary> {
  const q = new URLSearchParams();
  if (agentId != null) q.set("agent_id", String(agentId));
  return request(`/api/dashboard/summary${q.size ? `?${q}` : ""}`);
}

export async function getCandidateProfiles(params: {
  job_id?: number; status?: string; search?: string;
  agent_id?: number | null; page?: number; page_size?: number;
}): Promise<PagedCandidates> {
  const q = new URLSearchParams();
  if (params.page)             q.set("page",       String(params.page));
  if (params.page_size)        q.set("page_size",  String(params.page_size));
  if (params.job_id != null)   q.set("job_id",     String(params.job_id));
  if (params.status)           q.set("status",     params.status);
  if (params.search)           q.set("search",     params.search);
  if (params.agent_id != null) q.set("agent_id",   String(params.agent_id));
  return request(`/api/candidate-profiles?${q}`);
}

// export async function getCandidateProfileDetail(
//   candidateId: number, agentId?: number | null,
// ): Promise<CandidateProfileDetail> {
//   const q = new URLSearchParams();
//   if (agentId != null) q.set("agent_id", String(agentId));
//   return request(`/api/candidate-profiles/${candidateId}${q.size ? `?${q}` : ""}`);
// }

// ─── Jobs  (backend cap: le=500) ─────────────────────────────────────
export async function getJobs(params: {
  search?: string; keyword?: string; company?: string;
  researched?: boolean; agent_id?: number | null;
  page?: number; page_size?: number;
}): Promise<PagedJobs> {
  const q = new URLSearchParams();
  if (params.page)       q.set("page",       String(params.page));
  if (params.page_size)  q.set("page_size",  String(params.page_size));
  if (params.search)     q.set("search",     params.search);
  if (params.keyword)    q.set("keyword",    params.keyword);
  if (params.company)    q.set("company",    params.company);
  if (params.researched != null) q.set("researched", String(params.researched));
  if (params.agent_id != null)   q.set("agent_id",   String(params.agent_id));
  return request(`/api/jobs?${q}`);
}

// ─── Companies  (backend cap: le=500) ────────────────────────────────
export async function getCompanies(params: {
  status?: string; search?: string; agent_id?: number | null;
  page?: number; page_size?: number;
}): Promise<PagedCompanies> {
  const q = new URLSearchParams();
  if (params.page)      q.set("page",      String(params.page));
  if (params.page_size) q.set("page_size", String(params.page_size));
  if (params.status)    q.set("status",    params.status);
  if (params.search)    q.set("search",    params.search);
  if (params.agent_id != null) q.set("agent_id", String(params.agent_id));
  return request(`/api/companies?${q}`);
}

export async function getCompanyDetail(
  companyId: number, agentId?: number | null
): Promise<{ company: CompanyRow; prospects: ProspectRow[] }> {
  const q = new URLSearchParams();
  if (agentId != null) q.set("agent_id", String(agentId));
  return request(`/api/companies/${companyId}${q.size ? `?${q}` : ""}`);
}

// ─── Prospects  (backend cap: le=500) ────────────────────────────────
export async function getProspects(params: {
  search?: string; bucket?: string; company_id?: number;
  agent_id?: number | null; page?: number; page_size?: number;
}): Promise<PagedProspects> {
  const q = new URLSearchParams();
  if (params.page)       q.set("page",       String(params.page));
  if (params.page_size)  q.set("page_size",  String(params.page_size));
  if (params.search)     q.set("search",     params.search);
  if (params.bucket)     q.set("bucket",     params.bucket);
  if (params.company_id != null) q.set("company_id", String(params.company_id));
  if (params.agent_id != null)   q.set("agent_id",   String(params.agent_id));
  return request(`/api/prospects?${q}`);
}

export async function getProspectDetail(prospectId: number, agentId?: number | null): Promise<ProspectDetail> {
  const q = new URLSearchParams();
  if (agentId != null) q.set("agent_id", String(agentId));
  return request(`/api/prospects/${prospectId}${q.size ? `?${q}` : ""}`);
}

// ─── Runs  (backend cap: le=200) ─────────────────────────────────────
export async function getRuns(
  page = 1, pageSize = 40, agentId?: number | null
): Promise<PagedRuns> {
  const q = new URLSearchParams({ page: String(page), page_size: String(pageSize) });
  if (agentId != null) q.set("agent_id", String(agentId));
  return request(`/api/runs?${q}`);
}

export async function getRunDetail(runId: number): Promise<PipelineRun> {
  return request(`/api/runs/${runId}`);
}

export async function triggerRun(
  pipeline: PipelineType, options?: { agent_id?: number | null }
): Promise<{ run_id: number; status: string }> {
  return request("/api/runs/trigger", {
    method: "POST",
    body: JSON.stringify({ pipeline, agent_id: options?.agent_id }),
  });
}

// ─── Keywords ─────────────────────────────────────────────────────────
export async function getKeywords(
  page = 1,
  pageSize = 50,
): Promise<{ items: KeywordRow[]; total: number; page: number; page_size: number }> {
  return request(`/api/keywords?page=${page}&page_size=${pageSize}`);
}

export async function createKeyword(payload: {
  keyword: string; location?: string | null; max_job_age_days: number; max_jobs: number; active: boolean;
}): Promise<{ id: number }> {
  return request("/api/keywords", { method: "POST", body: JSON.stringify(payload) });
}
export async function updateKeyword(
  keywordId: number,
  payload: { keyword: string; location?: string | null; max_job_age_days: number; max_jobs: number; active: boolean },
): Promise<{ ok: boolean }> {
  return request(`/api/keywords/${keywordId}`, { method: "PUT", body: JSON.stringify(payload) });
}
export async function deleteKeyword(keywordId: number): Promise<{ ok: boolean }> {
  return request(`/api/keywords/${keywordId}`, { method: "DELETE" });
}

// ─── LinkedIn credentials ─────────────────────────────────────────────
export async function getLinkedinCredentials(): Promise<{ items: LinkedInCredentialRow[] }> {
  return request("/api/linkedin-credentials");
}
export async function upsertLinkedinCredentials(payload: {
  email: string; password: string; priority: number; active: boolean;
}): Promise<{ id: number }> {
  return request("/api/linkedin-credentials/upsert", { method: "POST", body: JSON.stringify(payload) });
}
export async function deactivateLinkedinCredential(id: number): Promise<{ ok: boolean }> {
  return request(`/api/linkedin-credentials/${id}/deactivate`, { method: "POST" });
}

// ─── Settings ─────────────────────────────────────────────────────────
export async function getSettings(): Promise<{ items: SettingRow[] }> {
  return request("/api/settings");
}
export async function upsertSetting(key: string, value: unknown, description?: string): Promise<{ ok: boolean }> {
  return request("/api/settings", {
    method: "PUT",
    body: JSON.stringify({ key, value, description }),
  });
}



// export async function getCandidateProfileDetail(
//   candidateId: number, agentId?: number | null,
// ): Promise<CandidateProfileDetail> {
//   const q = new URLSearchParams();
//   if (agentId != null) q.set("agent_id", String(agentId));
//   return request(`/api/candidate-profiles/${candidateId}${q.size ? `?${q}` : ""}`);
// }