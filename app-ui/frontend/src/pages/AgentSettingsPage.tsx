import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import {
  deleteAgentKeyword,
  getAgent,
  getAgentKeywords,
  getAgentProfile,
  updateAgent,
  updateAgentProfile,
  upsertAgentKeyword,
} from "../api/client";
import type { AgentDefinitionRow, AgentKeywordRow, AgentProfilePayload } from "../api/types";
import { AppIcon, SectionCard, Spinner } from "../components/index";

type Tab = "basic" | "profile" | "keywords";
const KEYWORD_TYPES = ["job_title", "seniority", "skill", "industry", "exclude"] as const;

export function AgentSettingsPage() {
  const { agentId } = useParams<{ agentId: string }>();
  const id = Number(agentId);
  const [activeTab, setActiveTab] = useState<Tab>("basic");

  return (
    <div className="page-stack fade-in">
      <div className="page-header">
        <div>
          <h1>Agent Configuration</h1>
          <p className="page-sub">Manage this agent's identity, sales profile, and targeting keywords.</p>
        </div>
      </div>

      <div className="settings-shell">
        <div className="settings-nav">
          {([
            { id: "basic",    label: "Basic Info",    icon: "agents"    },
            { id: "profile",  label: "Sales Profile", icon: "prospects" },
            { id: "keywords", label: "Keywords",      icon: "leads"     },
          ] as const).map((tab) => (
            <button
              key={tab.id}
              className={`settings-nav-btn ${activeTab === tab.id ? "active" : ""}`}
              onClick={() => setActiveTab(tab.id)}
            >
              <AppIcon name={tab.icon} size={16} />
              {tab.label}
            </button>
          ))}
        </div>

        <div>
          {activeTab === "basic"    && <BasicInfoTab    agentId={id} />}
          {activeTab === "profile"  && <SalesProfileTab agentId={id} />}
          {activeTab === "keywords" && <KeywordsTab     agentId={id} />}
        </div>
      </div>
    </div>
  );
}

/* ── BasicInfoTab ──────────────────────────────────────────────────── */
function BasicInfoTab({ agentId }: { agentId: number }) {
  const [agent,   setAgent]   = useState<AgentDefinitionRow | null>(null);
  const [form,    setForm]    = useState({ name: "", description: "", status: "active" });
  const [loading, setLoading] = useState(true);
  const [saving,  setSaving]  = useState(false);
  const [error,   setError]   = useState("");
  const [success, setSuccess] = useState(false);

  useEffect(() => {
    setLoading(true);
    setError("");
    getAgent(agentId)
      .then((a) => {
        setAgent(a);
        setForm({ name: a.name ?? "", description: a.description ?? "", status: a.status ?? "active" });
      })
      .catch((err) => setError(err instanceof Error ? err.message : "Failed to load agent"))
      .finally(() => setLoading(false));
  }, [agentId]);

  const save = async () => {
    if (!form.name.trim()) { setError("Agent name is required"); return; }
    setSaving(true);
    setError("");
    setSuccess(false);
    try {
      await updateAgent(agentId, {
        name:        form.name.trim(),
        description: form.description.trim() || undefined,
        status:      form.status,
      });
      setSuccess(true);
      setTimeout(() => setSuccess(false), 3000);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Save failed");
    } finally {
      setSaving(false);
    }
  };

  if (loading) return <Loading />;

  return (
    <SectionCard title="Basic Information" subtitle="Agent identity and operational status">
      <div style={{ display: "flex", flexDirection: "column", gap: 20 }}>
        {error   && <div className="error-banner">{error}</div>}
        {success && <SavedBanner />}

        <div className="form-group">
          <label className="form-label">Agent Name *</label>
          <input className="form-input" value={form.name}
            onChange={(e) => setForm((p) => ({ ...p, name: e.target.value }))}
            placeholder="Staffing Sales Agent" />
        </div>

        <div className="form-group">
          <label className="form-label">Description</label>
          <textarea className="form-input form-textarea" rows={3} value={form.description}
            onChange={(e) => setForm((p) => ({ ...p, description: e.target.value }))}
            placeholder="Describe what this agent does…" />
        </div>

        <div className="form-group">
          <label className="form-label">Status</label>
          <select className="form-select" value={form.status}
            onChange={(e) => setForm((p) => ({ ...p, status: e.target.value }))}>
            <option value="active">Active</option>
            <option value="paused">Paused</option>
            <option value="archived">Archived</option>
          </select>
          <span className="form-hint">Paused agents skip automated pipeline scheduling.</span>
        </div>

        <div style={{ borderTop: "1px solid var(--border)", paddingTop: 16 }}>
          <button className="btn-primary" onClick={save} disabled={saving}>
            {saving ? <><Spinner size={13} /> Saving…</> : <><AppIcon name="check" size={13} /> Save Changes</>}
          </button>
        </div>

        {agent && (
          <div style={{ borderTop: "1px solid var(--border)", paddingTop: 16, display: "flex", flexDirection: "column", gap: 8 }}>
            <span className="form-label">Read-only Info</span>
            {[
              ["Agent ID",   String(agent.id)],
              ["Agent Key",  agent.agent_key],
              ["Type",       agent.agent_type ?? "sales"],
              ["Profile v.", String(agent.current_profile_version ?? "—")],
              ["Created",    agent.created_at_utc ? new Date(String(agent.created_at_utc)).toLocaleDateString() : "—"],
            ].map(([label, value]) => (
              <div key={label} style={{ display: "flex", justifyContent: "space-between", fontSize: "0.82rem" }}>
                <span style={{ color: "var(--ink-2)" }}>{label}</span>
                <span style={{ fontFamily: "var(--font-mono)", fontSize: "0.78rem", color: "var(--ink-0)", fontWeight: 600 }}>{value}</span>
              </div>
            ))}
          </div>
        )}
      </div>
    </SectionCard>
  );
}

/* ── SalesProfileTab ───────────────────────────────────────────────── */
function SalesProfileTab({ agentId }: { agentId: number }) {
  const empty = {
    persona_title: "", domain_focus: "", service_offering: "",
    sales_objective: "", target_buyer_roles: [] as string[], value_outcomes: [] as string[],
  };
  const [form,     setForm]     = useState(empty);
  const [tagInput, setTagInput] = useState({ target_buyer_roles: "", value_outcomes: "" });
  const [version,  setVersion]  = useState(0);
  const [loading,  setLoading]  = useState(true);
  const [saving,   setSaving]   = useState(false);
  const [error,    setError]    = useState("");
  const [success,  setSuccess]  = useState(false);

  useEffect(() => {
    setLoading(true);
    setError("");
    getAgentProfile(agentId)
      .then((res) => {
        const p = res.profile ?? {};
        setVersion(p.version ?? 0);
        setForm({
          persona_title:      p.persona_title      ?? "",
          domain_focus:       p.domain_focus       ?? "",
          service_offering:   p.service_offering   ?? "",
          sales_objective:    p.sales_objective    ?? "",
          target_buyer_roles: Array.isArray(p.target_buyer_roles) ? p.target_buyer_roles : [],
          value_outcomes:     Array.isArray(p.value_outcomes)     ? p.value_outcomes     : [],
        });
      })
      .catch((err) => setError(err instanceof Error ? err.message : "Failed to load profile"))
      .finally(() => setLoading(false));
  }, [agentId]);

  const addTag = (field: "target_buyer_roles" | "value_outcomes") => {
    const val = tagInput[field].trim();
    if (!val) return;
    setForm((p) => ({ ...p, [field]: [...p[field], val] }));
    setTagInput((p) => ({ ...p, [field]: "" }));
  };

  const removeTag = (field: "target_buyer_roles" | "value_outcomes", idx: number) =>
    setForm((p) => ({ ...p, [field]: p[field].filter((_, i) => i !== idx) }));

  const save = async () => {
    setSaving(true);
    setError("");
    setSuccess(false);
    try {
      // Send only the fields the backend ProfileUpdate model accepts
      const payload: AgentProfilePayload = {
        persona_title:      form.persona_title.trim()    || undefined,
        domain_focus:       form.domain_focus.trim()     || undefined,
        service_offering:   form.service_offering.trim() || undefined,
        sales_objective:    form.sales_objective.trim()  || undefined,
        target_buyer_roles: form.target_buyer_roles.length ? form.target_buyer_roles : undefined,
        value_outcomes:     form.value_outcomes.length   ? form.value_outcomes     : undefined,
      };
      await updateAgentProfile(agentId, payload);
      setVersion((v) => v + 1);
      setSuccess(true);
      setTimeout(() => setSuccess(false), 3000);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Save failed");
    } finally {
      setSaving(false);
    }
  };

  if (loading) return <Loading />;

  return (
    <SectionCard title="Sales Profile" subtitle={`Define targeting persona and objectives · version ${version}`}>
      <div style={{ display: "flex", flexDirection: "column", gap: 20 }}>
        {error   && <div className="error-banner">{error}</div>}
        {success && <SavedBanner />}

        <div className="form-row cols-2">
          <div className="form-group">
            <label className="form-label">Persona Title</label>
            <input className="form-input" value={form.persona_title}
              onChange={(e) => setForm((p) => ({ ...p, persona_title: e.target.value }))}
              placeholder="e.g. Staffing Sales Director" />
            <span className="form-hint">How the agent introduces itself in outreach.</span>
          </div>
          <div className="form-group">
            <label className="form-label">Domain Focus</label>
            <input className="form-input" value={form.domain_focus}
              onChange={(e) => setForm((p) => ({ ...p, domain_focus: e.target.value }))}
              placeholder="e.g. Technical Staffing, IT Recruitment" />
          </div>
        </div>

        <div className="form-group">
          <label className="form-label">Service Offering</label>
          <textarea className="form-input form-textarea" rows={3} value={form.service_offering}
            onChange={(e) => setForm((p) => ({ ...p, service_offering: e.target.value }))}
            placeholder="Describe the staffing / recruitment services offered…" />
          <span className="form-hint">Used by the LLM to craft personalised outreach messages.</span>
        </div>

        <div className="form-group">
          <label className="form-label">Sales Objective</label>
          <textarea className="form-input form-textarea" rows={2} value={form.sales_objective}
            onChange={(e) => setForm((p) => ({ ...p, sales_objective: e.target.value }))}
            placeholder="e.g. Schedule a discovery call with Hiring Managers to discuss technical hiring challenges…" />
        </div>

        <TagField
          label="Target Buyer Roles"
          hint="Job titles of decision-makers to contact (e.g. CTO, VP Engineering, Head of Talent)"
          tags={form.target_buyer_roles}
          input={tagInput.target_buyer_roles}
          onInputChange={(v) => setTagInput((p) => ({ ...p, target_buyer_roles: v }))}
          onAdd={() => addTag("target_buyer_roles")}
          onRemove={(i) => removeTag("target_buyer_roles", i)}
        />

        <TagField
          label="Value Outcomes"
          hint="Benefits you deliver (e.g. Reduce time-to-hire by 40%, Access pre-vetted senior talent)"
          tags={form.value_outcomes}
          input={tagInput.value_outcomes}
          onInputChange={(v) => setTagInput((p) => ({ ...p, value_outcomes: v }))}
          onAdd={() => addTag("value_outcomes")}
          onRemove={(i) => removeTag("value_outcomes", i)}
        />

        <div style={{ borderTop: "1px solid var(--border)", paddingTop: 16 }}>
          <button className="btn-primary" onClick={save} disabled={saving}>
            {saving ? <><Spinner size={13} /> Saving…</> : <><AppIcon name="check" size={13} /> Save Profile</>}
          </button>
        </div>
      </div>
    </SectionCard>
  );
}

/* ── KeywordsTab ───────────────────────────────────────────────────── */
function KeywordsTab({ agentId }: { agentId: number }) {
  const [keywords, setKeywords] = useState<AgentKeywordRow[]>([]);
  const [loading,  setLoading]  = useState(true);
  const [saving,   setSaving]   = useState(false);
  const [error,    setError]    = useState("");
  const [form, setForm] = useState({
    keyword_type: "job_title" as string,
    keyword: "",
    weight: 1.0,
    active: true,
  });

  const load = () => {
    setLoading(true);
    getAgentKeywords(agentId)
      .then((r) => setKeywords(r.items))
      .catch((err) => setError(err instanceof Error ? err.message : "Failed to load keywords"))
      .finally(() => setLoading(false));
  };
  useEffect(load, [agentId]);

  const submit = async () => {
    if (!form.keyword.trim()) { setError("Keyword cannot be empty"); return; }
    setSaving(true);
    setError("");
    try {
      await upsertAgentKeyword(agentId, {
        keyword_type: form.keyword_type,
        keyword:      form.keyword.trim(),
        weight:       form.weight,
        active:       form.active,
      });
      setForm((p) => ({ ...p, keyword: "" }));
      load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to save keyword");
    } finally {
      setSaving(false);
    }
  };

  const remove = async (kwId: number) => {
    setError("");
    try {
      await deleteAgentKeyword(agentId, kwId);
      load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to delete keyword");
    }
  };

  const grouped = KEYWORD_TYPES.reduce<Record<string, AgentKeywordRow[]>>((acc, t) => {
    acc[t] = keywords.filter((k) => k.keyword_type === t);
    return acc;
  }, {});

  const typeColors: Record<string, string> = {
    job_title: "var(--blue)", seniority: "var(--violet)", skill: "var(--teal)",
    industry: "var(--amber)", exclude: "var(--danger)",
  };

  return (
    <SectionCard title="Targeting Keywords" subtitle="Keywords shape which leads are ingested and which prospects are prioritised by the LLM">
      <div style={{ display: "flex", flexDirection: "column", gap: 24 }}>
        {error && <div className="error-banner">{error}</div>}

        {/* Add form */}
        <div style={{
          display: "flex", gap: 10, flexWrap: "wrap", alignItems: "flex-end",
          padding: 16, background: "var(--bg-subtle)", borderRadius: "var(--r-lg)", border: "1px solid var(--border)",
        }}>
          <div className="form-group" style={{ flex: "1 1 140px" }}>
            <label className="form-label">Type</label>
            <select className="form-select" value={form.keyword_type}
              onChange={(e) => setForm((p) => ({ ...p, keyword_type: e.target.value }))}>
              {KEYWORD_TYPES.map((t) => <option key={t} value={t}>{t.replace("_", " ")}</option>)}
            </select>
          </div>
          <div className="form-group" style={{ flex: "2 1 200px" }}>
            <label className="form-label">Keyword</label>
            <input className="form-input" value={form.keyword}
              onChange={(e) => setForm((p) => ({ ...p, keyword: e.target.value }))}
              onKeyDown={(e) => e.key === "Enter" && void submit()}
              placeholder="e.g. Software Engineer" />
          </div>
          <div className="form-group" style={{ flex: "0 0 80px" }}>
            <label className="form-label">Weight</label>
            <input className="form-input" type="number" min={0.1} max={10} step={0.1} value={form.weight}
              onChange={(e) => setForm((p) => ({ ...p, weight: Number(e.target.value) }))} />
          </div>
          <div className="form-group" style={{ flex: "0 0 auto" }}>
            <label className="form-label">Active</label>
            <select className="form-select" value={form.active ? "1" : "0"}
              onChange={(e) => setForm((p) => ({ ...p, active: e.target.value === "1" }))}>
              <option value="1">Yes</option>
              <option value="0">No</option>
            </select>
          </div>
          <button className="btn-primary" onClick={submit} disabled={saving || !form.keyword.trim()} style={{ marginBottom: 1 }}>
            {saving ? <Spinner size={13} /> : <AppIcon name="plus" size={13} />} Add
          </button>
        </div>

        {/* Keyword list grouped by type */}
        {loading ? (
          <Loading />
        ) : keywords.length === 0 ? (
          <div className="empty-state" style={{ padding: "32px" }}>
            <AppIcon name="key" size={28} />
            <p>No keywords configured. Add targeting keywords above to guide the agent's lead and prospect selection logic.</p>
          </div>
        ) : (
          KEYWORD_TYPES.filter((t) => grouped[t]?.length > 0).map((type) => (
            <div key={type}>
              <div style={{
                display: "flex", alignItems: "center", gap: 8, marginBottom: 8,
                fontSize: "0.72rem", fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.07em",
              }}>
                <div style={{ width: 8, height: 8, borderRadius: 2, background: typeColors[type] ?? "var(--ink-3)" }} />
                <span style={{ color: "var(--ink-3)" }}>{type.replace("_", " ")}</span>
                <span style={{ color: "var(--ink-3)", fontWeight: 400 }}>({grouped[type].length})</span>
              </div>
              <div style={{ border: "1px solid var(--border)", borderRadius: "var(--r-md)", overflow: "hidden" }}>
                {grouped[type].map((kw, i) => (
                  <div key={kw.id} style={{
                    display: "flex", alignItems: "center", gap: 12,
                    padding: "10px 14px",
                    borderTop: i > 0 ? "1px solid var(--border)" : "none",
                    background: "var(--surface)",
                  }}>
                    <div style={{
                      width: 6, height: 6, borderRadius: "50%",
                      background: kw.active ? (typeColors[type] ?? "var(--success)") : "var(--ink-3)",
                      flexShrink: 0,
                    }} />
                    <span style={{ flex: 1, fontWeight: 600, fontSize: "0.85rem", color: "var(--ink-0)" }}>{kw.keyword}</span>
                    <span style={{ fontSize: "0.72rem", color: "var(--ink-3)", fontFamily: "var(--font-mono)" }}>×{kw.weight}</span>
                    <span className={`status-pill ${kw.active ? "active" : "paused"}`}>{kw.active ? "active" : "off"}</span>
                    <button className="btn-icon danger" onClick={() => void remove(kw.id)} title="Delete keyword">
                      <AppIcon name="trash" size={12} />
                    </button>
                  </div>
                ))}
              </div>
            </div>
          ))
        )}
      </div>
    </SectionCard>
  );
}

/* ── Shared helpers ─────────────────────────────────────────────── */
function TagField({
  label, hint, tags, input, onInputChange, onAdd, onRemove,
}: {
  label: string; hint?: string; tags: string[];
  input: string; onInputChange: (v: string) => void;
  onAdd: () => void; onRemove: (i: number) => void;
}) {
  return (
    <div className="form-group">
      <label className="form-label">{label}</label>
      {hint && <span className="form-hint" style={{ marginBottom: 6, display: "block" }}>{hint}</span>}
      <div className="tag-list" style={{ marginBottom: 8 }}>
        {tags.map((tag, i) => (
          <span key={i} className="tag">
            {tag}
            <button className="tag-remove" onClick={() => onRemove(i)}>×</button>
          </span>
        ))}
        {tags.length === 0 && (
          <span style={{ fontSize: "0.78rem", color: "var(--ink-3)", fontStyle: "italic" }}>None added yet</span>
        )}
      </div>
      <div style={{ display: "flex", gap: 8 }}>
        <input
          className="form-input"
          style={{ flex: 1 }}
          value={input}
          onChange={(e) => onInputChange(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && (e.preventDefault(), onAdd())}
          placeholder="Type and press Enter…"
        />
        <button className="btn-secondary" onClick={onAdd} disabled={!input.trim()}>
          <AppIcon name="plus" size={13} /> Add
        </button>
      </div>
    </div>
  );
}

function Loading() {
  return (
    <div style={{ display: "flex", gap: 8, alignItems: "center", color: "var(--ink-3)", padding: "24px" }}>
      <Spinner size={16} /> Loading…
    </div>
  );
}

function SavedBanner() {
  return (
    <div style={{
      background: "var(--success-soft)", border: "1px solid var(--success-border)",
      color: "var(--success)", padding: "10px 16px", borderRadius: "var(--r-md)",
      fontSize: "0.84rem", fontWeight: 600,
    }}>
      ✓ Changes saved and applied successfully.
    </div>
  );
}
