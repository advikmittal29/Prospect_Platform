import { FormEvent, useEffect, useMemo, useState } from "react";
import {
  AGENT_SCOPE_EVENT,
  createAgent,
  deleteAgentKeyword,
  getActiveAgentId,
  getAgentKeywords,
  getAgentProfile,
  getAgents,
  setActiveAgentId,
  triggerRun,
  updateAgentProfile,
  upsertAgentKeyword,
} from "../api/client";
import type { AgentDefinitionRow, AgentKeywordRow, AgentProfileResponse, PipelineType } from "../api/types";
import { SectionCard } from "../components/SectionCard";

const pipelineTypes: PipelineType[] = ["ingest", "research", "intelligence", "candidate_hunt"];

const initCreateForm = {
  agent_key: "",
  name: "",
  description: "",
  agent_type: "custom",
  status: "active" as "active" | "paused" | "archived",
};

const initKeywordForm = {
  keyword_type: "title_include",
  keyword: "",
  weight: 1,
  active: true,
};

export function AgentsPage() {
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [agents, setAgents] = useState<AgentDefinitionRow[]>([]);
  const [selectedAgentId, setSelectedAgentId] = useState<number | null>(null);
  const [activeScopeId, setActiveScopeId] = useState<number | null>(getActiveAgentId());
  const [profileData, setProfileData] = useState<AgentProfileResponse | null>(null);
  const [keywords, setKeywords] = useState<AgentKeywordRow[]>([]);
  const [createForm, setCreateForm] = useState(initCreateForm);
  const [keywordForm, setKeywordForm] = useState(initKeywordForm);
  const [runningPipeline, setRunningPipeline] = useState<PipelineType | null>(null);

  const [profileForm, setProfileForm] = useState({
    persona_title: "",
    domain_focus: "",
    service_offering: "",
    sales_objective: "",
    target_buyer_roles: "",
    value_outcomes: "",
  });

  useEffect(() => {
    const onScopeChange = () => setActiveScopeId(getActiveAgentId());
    window.addEventListener(AGENT_SCOPE_EVENT, onScopeChange);
    return () => window.removeEventListener(AGENT_SCOPE_EVENT, onScopeChange);
  }, []);

  const selectedAgent = useMemo(
    () => agents.find((a) => a.id === selectedAgentId) ?? null,
    [agents, selectedAgentId]
  );

  const loadAgents = async () => {
    setLoading(true);
    setError("");
    try {
      const response = await getAgents();
      const items = response.items ?? [];
      setAgents(items);
      const savedId = getActiveAgentId();
      const stillExists = items.some((a) => a.id === selectedAgentId);
      const savedExists = items.some((a) => a.id === savedId);
      const activeAgent = items.find((a) => a.status === "active");
      const nextSelected =
        stillExists && selectedAgentId
          ? selectedAgentId
          : savedExists && savedId
            ? savedId
            : activeAgent?.id ?? items[0]?.id ?? null;
      setSelectedAgentId(nextSelected);
      if (!savedExists && nextSelected) {
        setActiveAgentId(nextSelected);
        setActiveScopeId(nextSelected);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load agents");
    } finally {
      setLoading(false);
    }
  };

  const loadAgentDetail = async (agentId: number) => {
    setBusy(true);
    setError("");
    try {
      const [profileResp, keywordResp] = await Promise.all([
        getAgentProfile(agentId),
        getAgentKeywords(agentId),
      ]);
      setProfileData(profileResp);
      setKeywords(keywordResp.items ?? []);
      setProfileForm({
        persona_title: profileResp.profile.persona_title ?? "",
        domain_focus: profileResp.profile.domain_focus ?? "",
        service_offering: profileResp.profile.service_offering ?? "",
        sales_objective: profileResp.profile.sales_objective ?? "",
        target_buyer_roles: (profileResp.profile.target_buyer_roles ?? []).join(", "),
        value_outcomes: (profileResp.profile.value_outcomes ?? []).join(", "),
      });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load selected agent details");
    } finally {
      setBusy(false);
    }
  };

  useEffect(() => {
    void loadAgents();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (!selectedAgentId) {
      setProfileData(null);
      setKeywords([]);
      return;
    }
    void loadAgentDetail(selectedAgentId);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedAgentId]);

  const saveProfile = async (e: FormEvent) => {
    e.preventDefault();
    if (!selectedAgentId) return;
    try {
      await updateAgentProfile(selectedAgentId, {
        persona_title: profileForm.persona_title || null,
        domain_focus: profileForm.domain_focus || null,
        service_offering: profileForm.service_offering || null,
        sales_objective: profileForm.sales_objective || null,
        target_buyer_roles: profileForm.target_buyer_roles
          .split(",")
          .map((x) => x.trim())
          .filter(Boolean),
        value_outcomes: profileForm.value_outcomes
          .split(",")
          .map((x) => x.trim())
          .filter(Boolean),
      });
      await loadAgentDetail(selectedAgentId);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to save profile");
    }
  };

  const createAgentSubmit = async (e: FormEvent) => {
    e.preventDefault();
    try {
      const created = await createAgent({
        agent_key: createForm.agent_key.trim(),
        name: createForm.name.trim(),
        description: createForm.description.trim() || undefined,
        agent_type: createForm.agent_type.trim() || "custom",
        status: createForm.status,
      });
      setCreateForm(initCreateForm);
      await loadAgents();
      setSelectedAgentId(created.id);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to create agent");
    }
  };

  const saveKeyword = async (e: FormEvent) => {
    e.preventDefault();
    if (!selectedAgentId) return;
    try {
      await upsertAgentKeyword(selectedAgentId, {
        keyword_type: keywordForm.keyword_type.trim().toLowerCase(),
        keyword: keywordForm.keyword.trim(),
        weight: Number(keywordForm.weight),
        active: Boolean(keywordForm.active),
      });
      setKeywordForm(initKeywordForm);
      await loadAgentDetail(selectedAgentId);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to save keyword");
    }
  };

  const removeKeyword = async (keywordId: number) => {
    if (!selectedAgentId) return;
    try {
      await deleteAgentKeyword(selectedAgentId, keywordId);
      await loadAgentDetail(selectedAgentId);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to delete keyword");
    }
  };

  const runPipeline = async (pipeline: PipelineType) => {
    if (!selectedAgentId) return;
    setRunningPipeline(pipeline);
    try {
      await triggerRun(pipeline, { agent_id: selectedAgentId });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to trigger pipeline");
    } finally {
      setRunningPipeline(null);
    }
  };

  return (
    <div className="page-stack">
      <div className="page-header fade-in">
        <div className="page-header-left">
          <h1>Agents</h1>
          <p className="page-sub">Manage multi-agent configuration, profile focus, and scoped execution.</p>
        </div>
      </div>

      {error ? (
        <div className="error-banner">
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" style={{ flexShrink: 0 }}>
            <circle cx="12" cy="12" r="10" />
            <path d="M12 8v4M12 16h.01" />
          </svg>
          {error}
        </div>
      ) : null}

      <SectionCard title="Agent Directory" subtitle="Choose which agent scope should drive dashboard and data views" noPad>
        <div className="table-wrap slim-height" style={{ border: "none", borderRadius: 0 }}>
          <table>
            <thead>
              <tr>
                <th>ID</th>
                <th>Key</th>
                <th>Name</th>
                <th>Status</th>
                <th>Type</th>
                <th>Persona</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {agents.map((agent) => (
                <tr key={agent.id} className={selectedAgentId === agent.id ? "row-active" : ""}>
                  <td className="cell-mono">#{agent.id}</td>
                  <td className="cell-mono">{agent.agent_key}</td>
                  <td className="cell-primary">{agent.name}</td>
                  <td>
                    <span className={`status-pill ${agent.status === "active" ? "completed" : "inactive"}`}>
                      {agent.status}
                    </span>
                  </td>
                  <td>{agent.agent_type ?? "-"}</td>
                  <td>{agent.persona_title ?? "-"}</td>
                  <td>
                    <div className="button-row">
                      <button className="btn-ghost btn-sm" onClick={() => setSelectedAgentId(agent.id)}>
                        Open
                      </button>
                      <button
                        className={`btn-sm ${activeScopeId === agent.id ? "btn-secondary" : "btn-primary"}`}
                        onClick={() => {
                          setActiveAgentId(agent.id);
                          setActiveScopeId(agent.id);
                        }}
                      >
                        {activeScopeId === agent.id ? "In Scope" : "Use Scope"}
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
              {agents.length === 0 ? (
                <tr>
                  <td colSpan={7} style={{ textAlign: "center", color: "var(--ink-2)", padding: "28px", fontStyle: "italic" }}>
                    {loading ? "Loading agents..." : "No agents found."}
                  </td>
                </tr>
              ) : null}
            </tbody>
          </table>
        </div>
      </SectionCard>

      <SectionCard title="Create Agent" subtitle="Add a new independent B2B motion agent">
        <form className="filter-grid" onSubmit={createAgentSubmit}>
          <label>
            Agent Key
            <input
              value={createForm.agent_key}
              onChange={(e) => setCreateForm((p) => ({ ...p, agent_key: e.target.value }))}
              placeholder="e.g. staffing_us"
              required
            />
          </label>
          <label>
            Agent Name
            <input
              value={createForm.name}
              onChange={(e) => setCreateForm((p) => ({ ...p, name: e.target.value }))}
              placeholder="e.g. Staffing Sales - US"
              required
            />
          </label>
          <label>
            Type
            <input
              value={createForm.agent_type}
              onChange={(e) => setCreateForm((p) => ({ ...p, agent_type: e.target.value }))}
              placeholder="custom"
            />
          </label>
          <label>
            Status
            <select
              value={createForm.status}
              onChange={(e) => setCreateForm((p) => ({ ...p, status: e.target.value as "active" | "paused" | "archived" }))}
            >
              <option value="active">active</option>
              <option value="paused">paused</option>
              <option value="archived">archived</option>
            </select>
          </label>
          <label>
            Description
            <input
              value={createForm.description}
              onChange={(e) => setCreateForm((p) => ({ ...p, description: e.target.value }))}
              placeholder="Optional note"
            />
          </label>
          <div className="form-actions">
            <button className="btn-primary" type="submit">Create Agent</button>
          </div>
        </form>
      </SectionCard>

      <SectionCard
        title={selectedAgent ? `Selected Agent: ${selectedAgent.name}` : "Selected Agent"}
        subtitle={selectedAgent ? `agent_key=${selectedAgent.agent_key}` : "Select an agent from the directory"}
      >
        {selectedAgent ? (
          <>
            <div className="pipeline-btn-group" style={{ marginBottom: 14 }}>
              {pipelineTypes.map((p) => (
                <button key={p} className={`pipeline-btn ${p}`} disabled={runningPipeline !== null} onClick={() => void runPipeline(p)}>
                  {runningPipeline === p ? "Running..." : `Run ${p}`}
                </button>
              ))}
            </div>

            <form className="filter-grid" onSubmit={saveProfile}>
              <label>
                Persona Title
                <input
                  value={profileForm.persona_title}
                  onChange={(e) => setProfileForm((p) => ({ ...p, persona_title: e.target.value }))}
                  placeholder="Sales persona identity"
                />
              </label>
              <label>
                Domain Focus
                <input
                  value={profileForm.domain_focus}
                  onChange={(e) => setProfileForm((p) => ({ ...p, domain_focus: e.target.value }))}
                  placeholder="Domain / vertical"
                />
              </label>
              <label>
                Service Offering
                <input
                  value={profileForm.service_offering}
                  onChange={(e) => setProfileForm((p) => ({ ...p, service_offering: e.target.value }))}
                  placeholder="What this agent sells"
                />
              </label>
              <label>
                Sales Objective
                <input
                  value={profileForm.sales_objective}
                  onChange={(e) => setProfileForm((p) => ({ ...p, sales_objective: e.target.value }))}
                  placeholder="Primary outcome target"
                />
              </label>
              <label>
                Target Buyer Roles (comma-separated)
                <input
                  value={profileForm.target_buyer_roles}
                  onChange={(e) => setProfileForm((p) => ({ ...p, target_buyer_roles: e.target.value }))}
                  placeholder="CTO, VP Engineering, Head of HR"
                />
              </label>
              <label>
                Value Outcomes (comma-separated)
                <input
                  value={profileForm.value_outcomes}
                  onChange={(e) => setProfileForm((p) => ({ ...p, value_outcomes: e.target.value }))}
                  placeholder="Faster hiring, lower attrition"
                />
              </label>
              <div className="form-actions">
                <button className="btn-primary" type="submit" disabled={busy}>Save Profile</button>
              </div>
            </form>
          </>
        ) : (
          <p className="muted">Pick an agent to view and edit scoped details.</p>
        )}
      </SectionCard>

      <SectionCard title="Agent Keywords" subtitle="Targeting terms used by this selected agent" noPad>
        {selectedAgentId ? (
          <>
            <div style={{ padding: "14px 16px", borderBottom: "1px solid var(--border)" }}>
              <form className="filter-grid" onSubmit={saveKeyword}>
                <label>
                  Keyword Type
                  <input
                    value={keywordForm.keyword_type}
                    onChange={(e) => setKeywordForm((p) => ({ ...p, keyword_type: e.target.value }))}
                    placeholder="title_include"
                    required
                  />
                </label>
                <label>
                  Keyword
                  <input
                    value={keywordForm.keyword}
                    onChange={(e) => setKeywordForm((p) => ({ ...p, keyword: e.target.value }))}
                    placeholder="Engineering Manager"
                    required
                  />
                </label>
                <label>
                  Weight
                  <input
                    type="number"
                    step="0.1"
                    min="0"
                    value={keywordForm.weight}
                    onChange={(e) => setKeywordForm((p) => ({ ...p, weight: Number(e.target.value) }))}
                  />
                </label>
                <label className="checkbox-line">
                  <input
                    type="checkbox"
                    checked={keywordForm.active}
                    onChange={(e) => setKeywordForm((p) => ({ ...p, active: e.target.checked }))}
                  />
                  Active
                </label>
                <div className="form-actions">
                  <button className="btn-primary" type="submit">Save Keyword</button>
                </div>
              </form>
            </div>

            <div className="table-wrap slim-height" style={{ border: "none", borderRadius: 0 }}>
              <table>
                <thead>
                  <tr>
                    <th>ID</th>
                    <th>Type</th>
                    <th>Keyword</th>
                    <th>Weight</th>
                    <th>Active</th>
                    <th></th>
                  </tr>
                </thead>
                <tbody>
                  {keywords.map((row) => (
                    <tr key={row.id}>
                      <td className="cell-mono">#{row.id}</td>
                      <td>{row.keyword_type}</td>
                      <td className="cell-primary">{row.keyword}</td>
                      <td className="cell-mono">{row.weight}</td>
                      <td>
                        <span className={`status-pill ${row.active ? "completed" : "inactive"}`}>
                          {row.active ? "active" : "inactive"}
                        </span>
                      </td>
                      <td>
                        <button className="btn-danger btn-sm" onClick={() => void removeKeyword(row.id)}>
                          Delete
                        </button>
                      </td>
                    </tr>
                  ))}
                  {keywords.length === 0 ? (
                    <tr>
                      <td colSpan={6} style={{ textAlign: "center", color: "var(--ink-2)", padding: "24px", fontStyle: "italic" }}>
                        No keywords configured for this agent.
                      </td>
                    </tr>
                  ) : null}
                </tbody>
              </table>
            </div>
          </>
        ) : (
          <div style={{ padding: "16px" }}>
            <p className="muted">Select an agent to manage its keyword strategy.</p>
          </div>
        )}
      </SectionCard>

      {profileData?.profile?.version ? (
        <p className="muted">Current profile version: v{profileData.profile.version}</p>
      ) : null}
    </div>
  );
}
