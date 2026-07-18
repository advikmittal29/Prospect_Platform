import { useEffect, useState } from "react";
import {
  deactivateLinkedinCredential,
  getLinkedinCredentials,
  getSettings,
  upsertLinkedinCredentials,
  upsertSetting,
} from "../api/client";
import type { LinkedInCredentialRow, SettingRow } from "../api/types";
import { AppIcon, SectionCard, Spinner } from "../components/index";

import type { KeywordRow } from "../api/types";
import {
  getKeywords, createKeyword, updateKeyword, deleteKeyword,
} from "../api/client";
import { Pagination } from "../components/Pagination";
import { Modal } from "../components/Modal";

/* ─── Category map (mirrors .env.example sections) ────────────────── */
type Category = {
  id: string;
  label: string;
  icon: string;
  color: string;
  prefix?: string[];
  keys?: string[];
  description: string;
};

const CATEGORIES: Category[] = [
  // {
  //   id: "database",
  //   label: "Database",
  //   icon: "key",
  //   color: "var(--blue)",
  //   prefix: ["DB_"],
  //   description: "SQL connection pool and logging settings",
  // },
  {
    id: "ingest",
    label: "Job Ingestion",
    icon: "leads",
    color: "var(--teal)",
    prefix: [],          // no flat INGEST_ keys — managed by CRUD grid below
    description: "Keywords, age limits, and throughput for job board ingestion",
  },
  // {
  //   id: "browser",
  //   label: "Naukri Browser",
  //   icon: "zap",
  //   color: "var(--violet)",
  //   prefix: ["BROWSER_"],
  //   description: "Playwright Chromium settings for Naukri scraping",
  // },
  // {
  //   id: "chrome",
  //   label: "LinkedIn Chrome",
  //   icon: "linkedin",
  //   color: "var(--blue)",
  //   prefix: ["CHROME_"],
  //   description: "Chrome CDP settings for LinkedIn research and scraping",
  // },
  {
    id: "llm",
    label: "LLM / AI",
    icon: "pulse",
    color: "var(--violet)",
    prefix: ["LLM_"],
    description: "Language model provider, model selection, and retry settings",
  },
  {
    id: "research",
    label: "Research Pipeline",
    icon: "companies",
    color: "var(--teal)",
    prefix: ["RESEARCH_"],
    description: "Batch sizes, prospect limits, and confidence thresholds for research runs",
  },
  // {
  //   id: "candidate",
  //   label: "Candidate Hunt",
  //   icon: "prospects",
  //   color: "var(--emerald)",
  //   prefix: ["CANDIDATE_HUNT_"],
  //   description: "Candidate search configuration, page limits, and scoring thresholds",
  // },
  // {
  //   id: "agent",
  //   label: "Agent Runtime",
  //   icon: "agents",
  //   color: "var(--blue)",
  //   prefix: ["AGENT_"],
  //   description: "Agent default key, runtime mode, and execution limits",
  // },
  {
    id: "outreach",
    label: "Outreach",
    icon: "zap",
    color: "var(--amber)",
    prefix: ["OUTREACH_", "LINKEDIN_OUTREACH_"],
    description: "Recruiter identity, channel selection, and test mode settings",
  },
  {
    id: "reply",
    label: "Reply Automation",
    icon: "refresh",
    color: "var(--emerald)",
    prefix: ["REPLY_"],
    description: "Sweep cadence, handoff caps, concurrency, quality gate, and live-session pacing",
  },
  {
    id: "smtp",
    label: "Email / SMTP",
    icon: "key",
    color: "var(--danger)",
    prefix: ["SMTP_"],
    keys: ["REPLY_HANDOFF_MANAGER_EMAIL"],
    description: "SMTP server, credentials, and TLS settings for email dispatch",
  },
  // {
  //   id: "other",
  //   label: "Other",
  //   icon: "settings",
  //   color: "var(--ink-3)",
  //   description: "Remaining platform configuration keys",
  // },
];

function categorizeSetting(key: string): string {
  const upper = key.toUpperCase();

  // An explicit `keys` listing pins a setting to a category regardless of its prefix.
  for (const cat of CATEGORIES) {
    if (cat.keys?.includes(upper)) return cat.id;
  }

  for (const cat of CATEGORIES) {
    if (!cat.prefix) continue;
    if (cat.prefix.some((p) => upper.startsWith(p))) return cat.id;
  }
  return "other";
}

/* ─── Component ───────────────────────────────────────────────────── */
export function SystemSettingsPage() {
  const [activeTab, setActiveTab] = useState("linkedin");

  return (
    <div className="page-stack fade-in">
      <div className="page-header">
        <div>
          <h1>System Settings</h1>
          <p className="page-sub">Platform-wide configuration, credentials, and runtime parameters.</p>
        </div>
      </div>

      <div className="settings-shell">
        {/* Left nav */}
        <div className="settings-nav">
          <button
            className={`settings-nav-btn ${activeTab === "linkedin" ? "active" : ""}`}
            onClick={() => setActiveTab("linkedin")}
          >
            <AppIcon name="linkedin" size={16} /> LinkedIn Accounts
          </button>
          <div style={{ height: 1, background: "var(--border)", margin: "8px 0" }} />
          {CATEGORIES.map((cat) => (
            <button
              key={cat.id}
              className={`settings-nav-btn ${activeTab === cat.id ? "active" : ""}`}
              onClick={() => setActiveTab(cat.id)}
            >
              <AppIcon name={cat.icon as any} size={15} />
              <span style={{ flex: 1, textAlign: "left" }}>{cat.label}</span>
            </button>
          ))}
        </div>

        {/* Right panel */}
        <div>
          {activeTab === "linkedin" && <LinkedInSection />}
         {activeTab === "ingest" && <JobIngestionPanel />}
          {activeTab !== "linkedin" && activeTab !== "ingest" && (
            <CategorySettingsPanel categoryId={activeTab} />
          )}
        </div>
      </div>
    </div>
  );
}

/* ─── LinkedIn credentials panel ─────────────────────────────────── */
function LinkedInSection() {
  const [creds, setCreds] = useState<LinkedInCredentialRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");
  const [form, setForm] = useState({ email: "", password: "", priority: 100, active: true });
  const [showPass, setShowPass] = useState(false);

  const load = () => {
    setLoading(true);
    getLinkedinCredentials()
      .then((r) => setCreds(r.items))
      .catch(() => setError("Failed to load credentials"))
      .finally(() => setLoading(false));
  };
  useEffect(load, []);

  const submit = async () => {
    if (!form.email || !form.password) { setError("Email and password are required"); return; }
    setSaving(true); setError(""); setSuccess("");
    try {
      await upsertLinkedinCredentials(form);
      setForm({ email: "", password: "", priority: 100, active: true });
      setSuccess("Credential saved successfully.");
      setTimeout(() => setSuccess(""), 3000);
      load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Save failed");
    } finally {
      setSaving(false);
    }
  };

  const deactivate = async (id: number) => {
    try { await deactivateLinkedinCredential(id); load(); }
    catch { setError("Failed to deactivate credential"); }
  };

  return (
    <SectionCard
      title="LinkedIn Accounts"
      subtitle="Managed accounts used for LinkedIn scraping and outreach dispatch. Passwords are stored encrypted."
    >
      <div style={{ display: "flex", flexDirection: "column", gap: 20 }}>
        {error && <div className="error-banner">{error}</div>}
        {success && <SuccessBanner msg={success} />}

        {/* Add form */}
        <div style={{
          display: "flex", gap: 10, flexWrap: "wrap", alignItems: "flex-end",
          padding: 16, background: "var(--bg-subtle)", borderRadius: "var(--r-lg)", border: "1px solid var(--border)",
        }}>
          <div className="form-group" style={{ flex: "2 1 200px" }}>
            <label className="form-label">Email / Username</label>
            <input className="form-input" type="email" value={form.email}
              onChange={(e) => setForm((p) => ({ ...p, email: e.target.value }))}
              placeholder="recruiter@company.com" />
          </div>
          <div className="form-group" style={{ flex: "2 1 180px" }}>
            <label className="form-label">Password</label>
            <div style={{ position: "relative" }}>
              <input
                className="form-input"
                type={showPass ? "text" : "password"}
                value={form.password}
                onChange={(e) => setForm((p) => ({ ...p, password: e.target.value }))}
                placeholder="••••••••"
                style={{ paddingRight: 36 }}
              />
              <button
                onClick={() => setShowPass((v) => !v)}
                style={{
                  position: "absolute", right: 8, top: "50%", transform: "translateY(-50%)",
                  background: "none", border: "none", cursor: "pointer", color: "var(--ink-3)", padding: 4,
                }}
              >
                <AppIcon name="eye" size={14} />
              </button>
            </div>
          </div>
          <div className="form-group" style={{ flex: "0 0 90px" }}>
            <label className="form-label">Priority</label>
            <input className="form-input" type="number" min={1} max={999} value={form.priority}
              onChange={(e) => setForm((p) => ({ ...p, priority: Number(e.target.value) }))} />
          </div>
          <div className="form-group" style={{ flex: "0 0 90px" }}>
            <label className="form-label">Active</label>
            <select className="form-select" value={form.active ? "1" : "0"}
              onChange={(e) => setForm((p) => ({ ...p, active: e.target.value === "1" }))}>
              <option value="1">Yes</option>
              <option value="0">No</option>
            </select>
          </div>
          <button className="btn-primary" onClick={submit} disabled={saving} style={{ marginBottom: 1 }}>
            {saving ? <Spinner size={13} /> : <AppIcon name="plus" size={13} />} Add / Update
          </button>
        </div>

        {/* Accounts table */}
        <div style={{ border: "1px solid var(--border)", borderRadius: "var(--r-md)", overflow: "hidden" }}>
          {loading ? (
            <div style={{ padding: 24, display: "flex", gap: 8, alignItems: "center", color: "var(--ink-3)" }}>
              <Spinner size={14} /> Loading accounts…
            </div>
          ) : creds.length === 0 ? (
            <div style={{ padding: 24, color: "var(--ink-3)", fontSize: "0.84rem", textAlign: "center" }}>
              No LinkedIn accounts configured. Add an account above to enable scraping and outreach.
            </div>
          ) : (
            <table className="data-table">
              <thead>
                <tr>
                  <th>Email</th>
                  <th>Priority</th>
                  <th>Status</th>
                  <th>Last Attempt</th>
                  <th>Last Success</th>
                  <th>Failure Reason</th>
                  <th style={{ width: 60 }}></th>
                </tr>
              </thead>
              <tbody>
                {creds.map((c) => (
                  <tr key={c.id}>
                    <td className="cell-primary">{c.email}</td>
                    <td className="cell-mono">{c.priority}</td>
                    <td><span className={`status-pill ${c.active ? "active" : "paused"}`}>{c.active ? "active" : "inactive"}</span></td>
                    <td className="cell-mono" style={{ fontSize: "0.74rem" }}>
                      {c.last_login_attempt_utc ? new Date(String(c.last_login_attempt_utc)).toLocaleDateString() : "—"}
                    </td>
                    <td className="cell-mono" style={{ fontSize: "0.74rem" }}>
                      {c.last_login_success_utc ? new Date(String(c.last_login_success_utc)).toLocaleDateString() : "—"}
                    </td>
                    <td style={{ fontSize: "0.74rem", color: "var(--danger)", maxWidth: 200 }}>
                      {c.last_login_failure_reason ?? "—"}
                    </td>
                    <td>
                      {c.active && (
                        <button className="btn-icon danger" onClick={() => void deactivate(c.id)} title="Deactivate">
                          <AppIcon name="close" size={12} />
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>
    </SectionCard>
  );
}
const EMPTY_KW = { keyword: "", location: "", max_job_age_days: 7, max_jobs: 50, active: true };

function JobIngestionPanel() {
  const [rows,    setRows]    = useState<KeywordRow[]>([]);
  const [total,   setTotal]   = useState(0);
  const [page,    setPage]    = useState(1);
  const [loading, setLoading] = useState(true);
  const [error,   setError]   = useState("");
  const [modal,   setModal]   = useState<{ mode: "create" | "edit"; row: Partial<KeywordRow> } | null>(null);
  const [saving,  setSaving]  = useState(false);
  const [deleteId, setDeleteId] = useState<number | null>(null);
  const PAGE_SIZE = 20;

  const load = (p = page) => {
    setLoading(true);
    getKeywords(p, PAGE_SIZE)
      .then((r) => { setRows(r.items); setTotal(r.total); setPage(p); })
      .catch(() => setError("Failed to load ingestion configs"))
      .finally(() => setLoading(false));
  };

  useEffect(() => { load(1); }, []);

  const openCreate = () => setModal({ mode: "create", row: { ...EMPTY_KW } });
  const openEdit   = (r: KeywordRow) => setModal({ mode: "edit", row: { ...r } });

  const save = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!modal) return;
    const r = modal.row;
    if (!String(r.keyword || "").trim()) return;
    setSaving(true); setError("");
    try {
      const payload = {
        keyword:          String(r.keyword || "").trim(),
        location:         String(r.location || "").trim() || undefined,
        max_job_age_days: Number(r.max_job_age_days) || 7,
        max_jobs:         Number(r.max_jobs) || 50,
        active:           !!r.active,
      };
      if (modal.mode === "create") await createKeyword(payload);
      else                         await updateKeyword(r.id!, payload);
      setModal(null);
      load(modal.mode === "create" ? 1 : page);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Save failed");
    } finally {
      setSaving(false);
    }
  };

  const confirmDelete = async () => {
    if (deleteId == null) return;
    try { await deleteKeyword(deleteId); setDeleteId(null); load(1); }
    catch { setError("Delete failed"); }
  };

  const f = modal?.row ?? {};

  const cat = CATEGORIES.find((c) => c.id === "ingest")!;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 0 }}>
      {/* Header */}
      <div style={{
        display: "flex", alignItems: "center", gap: 12,
        padding: "18px 20px",
        background: "var(--surface)",
        border: "1px solid var(--border)",
        borderRadius: "var(--r-xl) var(--r-xl) 0 0",
        borderBottom: "none",
      }}>
        <div style={{
          width: 38, height: 38, borderRadius: 10,
          background: cat.color + "18", color: cat.color,
          display: "flex", alignItems: "center", justifyContent: "center",
        }}>
          <AppIcon name={cat.icon as any} size={18} />
        </div>
        <div>
          <div style={{ fontWeight: 700, fontSize: "0.95rem", color: "var(--ink-0)" }}>Job Ingestion</div>
          <div style={{ fontSize: "0.77rem", color: "var(--ink-3)" }}>Each record defines one keyword + location to scrape</div>
        </div>
        <div style={{ marginLeft: "auto", display: "flex", alignItems: "center", gap: 12 }}>
          <span style={{ fontSize: "0.75rem", color: "var(--ink-3)" }}>{total} configs</span>
          <button className="btn-primary" style={{ display: "inline-flex", alignItems: "center", gap: 6, padding: "6px 14px", fontSize: "0.82rem" }} onClick={openCreate}>
            <AppIcon name="plus" size={13} /> Add Config
          </button>
        </div>
      </div>

      {/* Table */}
      <div style={{ background: "var(--surface)", border: "1px solid var(--border)", borderRadius: "0 0 var(--r-xl) var(--r-xl)", overflow: "hidden" }}>
        {error && <div className="error-banner" style={{ margin: 16, borderRadius: "var(--r-md)" }}>{error}</div>}

        {loading ? (
          <div style={{ padding: 32, display: "flex", gap: 8, alignItems: "center", color: "var(--ink-3)" }}>
            <Spinner size={16} /> Loading…
          </div>
        ) : rows.length === 0 ? (
          <div style={{ padding: "40px 24px", textAlign: "center", color: "var(--ink-3)", fontSize: "0.84rem" }}>
            No ingestion configs yet.<br />
            <button className="btn-primary" style={{ marginTop: 14, display: "inline-flex", alignItems: "center", gap: 6 }} onClick={openCreate}>
              <AppIcon name="plus" size={13} /> Add your first config
            </button>
          </div>
        ) : (
          <>
            <table className="data-table">
              <thead>
                <tr>
                  <th>Job Title / Keyword</th>
                  <th>Location</th>
                  <th style={{ width: 120, textAlign: "center" }}>Max Age (days)</th>
                  <th style={{ width: 130, textAlign: "center" }}>Max Records</th>
                  <th style={{ width: 80, textAlign: "center" }}>Active</th>
                  <th style={{ width: 150 }}>Last Run</th>
                  <th style={{ width: 80 }}></th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={r.id}>
                    <td className="cell-primary">{r.keyword}</td>
                    <td style={{ color: "var(--ink-2)", fontSize: "0.83rem" }}>{r.location || "—"}</td>
                    <td className="cell-mono" style={{ textAlign: "center" }}>{r.max_job_age_days}</td>
                    <td className="cell-mono" style={{ textAlign: "center" }}>{r.max_jobs}</td>
                    <td style={{ textAlign: "center" }}>
                      <span className={`status-pill ${r.active ? "active" : "paused"}`}>
                        {r.active ? "active" : "off"}
                      </span>
                    </td>
                    <td style={{ fontSize: "0.74rem", color: "var(--ink-3)" }}>
                      {r.last_run_utc ? new Date(r.last_run_utc).toLocaleString() : "—"}
                    </td>
                    <td>
                      <div style={{ display: "flex", gap: 4 }}>
                        <button className="btn-icon" onClick={() => openEdit(r)} title="Edit"><AppIcon name="edit" size={13} /></button>
                        <button className="btn-icon danger" onClick={() => setDeleteId(r.id)} title="Delete"><AppIcon name="close" size={13} /></button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            <div style={{ padding: "8px 12px", borderTop: "1px solid var(--border)" }}>
              <Pagination page={page} pageSize={PAGE_SIZE} totalItems={total} onPageChange={load} />
            </div>
          </>
        )}
      </div>

      {/* Create / Edit modal */}
      <Modal
        open={!!modal}
        onClose={() => setModal(null)}
        title={modal?.mode === "create" ? "Add Ingestion Config" : "Edit Ingestion Config"}
        subtitle="Each record defines one keyword + location combination to scrape jobs for."
        size="md"
      >
        {modal && (
          <form onSubmit={(e) => void save(e)} style={{ display: "flex", flexDirection: "column", gap: 14 }}>
            {error && <div className="error-banner">{error}</div>}
            <div className="form-group">
              <label className="form-label">Job Title / Keyword *</label>
              <input className="form-input" type="text" required
                value={String(f.keyword ?? "")}
                onChange={(e) => setModal((m) => m && { ...m, row: { ...m.row, keyword: e.target.value } })}
                placeholder="e.g. AI Engineer, Python Developer" />
            </div>
            <div className="form-group">
              <label className="form-label">Location <span style={{ color: "var(--ink-3)", fontWeight: 400 }}>(optional)</span></label>
              <input className="form-input" type="text"
                value={String(f.location ?? "")}
                onChange={(e) => setModal((m) => m && { ...m, row: { ...m.row, location: e.target.value } })}
                placeholder="e.g. Bangalore, Remote (blank = any)" />
            </div>
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
              <div className="form-group">
                <label className="form-label">Max Age (days)</label>
                <input className="form-input" type="number" min={1} max={365}
                  value={f.max_job_age_days ?? 7}
                  onChange={(e) => setModal((m) => m && { ...m, row: { ...m.row, max_job_age_days: Number(e.target.value) } })} />
              </div>
              <div className="form-group">
                <label className="form-label">Max Records to Fetch</label>
                <input className="form-input" type="number" min={1} max={5000}
                  value={f.max_jobs ?? 50}
                  onChange={(e) => setModal((m) => m && { ...m, row: { ...m.row, max_jobs: Number(e.target.value) } })} />
              </div>
            </div>
            <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
              <input type="checkbox" id="kw-active" checked={!!f.active}
                onChange={(e) => setModal((m) => m && { ...m, row: { ...m.row, active: e.target.checked } })}
                style={{ width: "auto", cursor: "pointer" }} />
              <label htmlFor="kw-active" className="form-label" style={{ marginBottom: 0, cursor: "pointer" }}>
                Active — include in ingestion runs
              </label>
            </div>
            <div style={{ display: "flex", gap: 8, paddingTop: 4 }}>
              <button className="btn-primary" type="submit" disabled={saving}>
                {saving ? <Spinner size={12} /> : null}
                {saving ? "Saving…" : modal.mode === "create" ? "Create" : "Save Changes"}
              </button>
              <button className="btn-secondary" type="button" onClick={() => setModal(null)}>Cancel</button>
            </div>
          </form>
        )}
      </Modal>

      {/* Delete confirmation */}
      <Modal
        open={deleteId != null}
        onClose={() => setDeleteId(null)}
        title="Delete Ingestion Config"
        size="sm"
      >
        <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
          <p style={{ fontSize: "0.85rem", color: "var(--ink-1)", margin: 0 }}>
            This ingestion config will be permanently removed. Jobs already fetched are unaffected.
          </p>
          <div style={{ display: "flex", gap: 8 }}>
            <button className="btn-primary" style={{ background: "var(--danger)" }} onClick={() => void confirmDelete()}>Delete</button>
            <button className="btn-secondary" onClick={() => setDeleteId(null)}>Cancel</button>
          </div>
        </div>
      </Modal>
    </div>
  );
}
/* ─── Category settings panel ─────────────────────────────────────── */
function CategorySettingsPanel({ categoryId }: { categoryId: string }) {
  const [allSettings, setAllSettings] = useState<SettingRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [edits, setEdits] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState<string | null>(null);
  const [successKey, setSuccessKey] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});

  useEffect(() => {
    setLoading(true);
    getSettings()
      .then((r) => {
        setAllSettings(r.items);
        const init: Record<string, string> = {};
        r.items.forEach((s) => { init[s.setting_key] = valueToString(s.setting_value); });
        setEdits(init);
      })
      .catch(() => setError("Failed to load settings"))
      .finally(() => setLoading(false));
  }, []);

  const cat = CATEGORIES.find((c) => c.id === categoryId);
  const categorySettings = allSettings.filter((s) => categorizeSetting(s.setting_key) === categoryId);

  const save = async (s: SettingRow) => {
    const key = s.setting_key;
    const rawVal = edits[key] ?? "";

    const problem = validateSetting(key, rawVal);
    if (problem) {
      setFieldErrors((p) => ({ ...p, [key]: problem }));
      return;
    }
    setFieldErrors((p) => { const n = { ...p }; delete n[key]; return n; });

    setSaving(key);
    setError("");
    try {
      const parsedValue = parseSettingValue(rawVal, s.config_type);
      await upsertSetting(key, parsedValue, s.description ?? undefined);
      setSuccessKey(key);
      setTimeout(() => setSuccessKey(null), 2500);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Save failed");
    } finally {
      setSaving(null);
    }
  };

  if (!cat) return null;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 0 }}>
      {/* Category header */}
      <div style={{
        display: "flex", alignItems: "center", gap: 12,
        padding: "18px 20px",
        background: "var(--surface)",
        border: "1px solid var(--border)",
        borderRadius: "var(--r-xl) var(--r-xl) 0 0",
        borderBottom: "none",
      }}>
        <div style={{
          width: 38, height: 38, borderRadius: 10,
          background: cat.color + "18", color: cat.color,
          display: "flex", alignItems: "center", justifyContent: "center",
        }}>
          <AppIcon name={cat.icon as any} size={18} />
        </div>
        <div>
          <div style={{ fontWeight: 700, fontSize: "0.95rem", color: "var(--ink-0)" }}>{cat.label}</div>
          <div style={{ fontSize: "0.77rem", color: "var(--ink-3)" }}>{cat.description}</div>
        </div>
        <div style={{ marginLeft: "auto", fontSize: "0.75rem", color: "var(--ink-3)" }}>
          {categorySettings.length} settings
        </div>
      </div>

      {/* Settings list */}
      <div style={{
        background: "var(--surface)",
        border: "1px solid var(--border)",
        borderRadius: "0 0 var(--r-xl) var(--r-xl)",
        overflow: "hidden",
      }}>
        {error && (
          <div className="error-banner" style={{ margin: 16, borderRadius: "var(--r-md)" }}>{error}</div>
        )}
        {loading ? (
          <div style={{ padding: 32, display: "flex", gap: 8, alignItems: "center", color: "var(--ink-3)" }}>
            <Spinner size={16} /> Loading settings…
          </div>
        ) : categorySettings.length === 0 ? (
          <div className="empty-state" style={{ padding: "32px" }}>
            <p>No settings found in this category.</p>
          </div>
        ) : (
          categorySettings.map((s, i) => (
            <SettingRow
              key={s.setting_key}
              setting={s}
              value={edits[s.setting_key] ?? ""}
              isFirst={i === 0}
              isSaving={saving === s.setting_key}
              isSaved={successKey === s.setting_key}
              fieldError={fieldErrors[s.setting_key]}
              onChange={(v) => setEdits((p) => ({ ...p, [s.setting_key]: v }))}
              onSave={() => void save(s)}
            />
          ))
        )}
      </div>
    </div>
  );
}

/* ─── Individual setting row ─────────────────────────────────────── */
function SettingRow({
  setting, value, isFirst, isSaving, isSaved, fieldError, onChange, onSave,
}: {
  setting: SettingRow;
  value: string;
  isFirst: boolean;
  isSaving: boolean;
  isSaved: boolean;
  fieldError?: string;
  onChange: (v: string) => void;
  onSave: () => void;
}) {
  const isBool = setting.config_type === "bool" || setting.config_type === "boolean" ||
    ["true", "false"].includes(String(setting.setting_value ?? "").toLowerCase());

  const isNum = setting.config_type === "int" || setting.config_type === "float" ||
    setting.config_type === "number";

  return (
    <div style={{
      display: "grid",
      gridTemplateColumns: "1fr minmax(200px, 280px) auto",
      gap: 14, alignItems: "start",
      padding: "14px 20px",
      borderTop: isFirst ? "none" : "1px solid var(--border)",
    }}>
      {/* Key + description */}
      <div>
        <div style={{
          fontFamily: "var(--font-mono)", fontSize: "0.78rem",
          fontWeight: 650, color: "var(--ink-0)", marginBottom: 3,
        }}>
          {setting.setting_key}
        </div>
        {setting.description && (
          <div style={{ fontSize: "0.72rem", color: "var(--ink-3)", lineHeight: 1.45 }}>
            {setting.description}
          </div>
        )}
        {setting.source === "catalog_default" && (
          <div style={{ fontSize: "0.67rem", color: "var(--ink-3)", marginTop: 3, fontStyle: "italic" }}>
            Using default value
          </div>
        )}
        {setting.updated_by && (
          <div style={{ fontSize: "0.67rem", color: "var(--ink-3)", marginTop: 2 }}>
            Last saved by {setting.updated_by}
          </div>
        )}
      </div>

      {/* Input */}
      <div>
        {isBool ? (
          <select
            className="form-select"
            value={value}
            onChange={(e) => onChange(e.target.value)}
            style={{ fontSize: "0.83rem" }}
          >
            <option value="true">true</option>
            <option value="false">false</option>
          </select>
        ) : isNum ? (
          <input
            className="form-input"
            type="number"
            value={value}
            onChange={(e) => onChange(e.target.value)}
            style={{ fontSize: "0.83rem", fontFamily: "var(--font-mono)" }}
          />
        ) : (
          <input
            className="form-input"
            value={value}
            onChange={(e) => onChange(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && onSave()}
            style={{
              fontSize: "0.83rem", fontFamily: "var(--font-mono)",
              ...(fieldError ? { borderColor: "var(--danger)" } : {}),
            }}
          />
        )}
        {fieldError && (
          <div style={{ fontSize: "0.7rem", color: "var(--danger)", marginTop: 5, lineHeight: 1.4 }}>
            {fieldError}
          </div>
        )}
      </div>

      {/* Save button */}
      <button
        className="btn-secondary"
        style={isSaved ? { color: "var(--success)", borderColor: "var(--success-border)" } : {}}
        onClick={onSave}
        disabled={isSaving}
      >
        {isSaving
          ? <Spinner size={12} />
          : isSaved
            ? <AppIcon name="check" size={12} />
            : <AppIcon name="edit" size={12} />}
        {isSaving ? "" : isSaved ? "Saved" : "Save"}
      </button>
    </div>
  );
}

/* ─── Value helpers ─────────────────────────────────────────────── */
function valueToString(v: unknown): string {
  if (v === null || v === undefined) return "";
  if (typeof v === "boolean") return String(v);
  if (typeof v === "number") return String(v);
  if (typeof v === "string") return v;
  if (Array.isArray(v)) return v.join(", ");
  return JSON.stringify(v);
}

/* Mirrors the backend guard in upsert_setting so the floor is explained before
   a round-trip. The scheduler clamps to 60s silently, so anything under a
   minute would look saved while quietly running at a different cadence. */
function validateSetting(key: string, raw: string): string | null {
  if (key === "REPLY_CHECK_INTERVAL_MINUTES") {
    const trimmed = raw.trim();
    const n = Number(trimmed);
    if (trimmed === "" || !Number.isFinite(n)) return "Enter a whole number of minutes.";
    if (!Number.isInteger(n)) return "Must be a whole number of minutes.";
    if (n < 1) return "Must be at least 1 — the scheduler enforces a 60-second floor.";
  }
  return null;
}

function parseSettingValue(raw: string, configType?: string | null): unknown {
  const type = (configType ?? "").toLowerCase();
  if (type === "bool" || type === "boolean") return raw === "true";
  if (type === "int" || type === "integer") return parseInt(raw, 10);
  if (type === "float" || type === "number") return parseFloat(raw);
  if (type === "list" || type === "array") {
    return raw.split(",").map((v) => v.trim()).filter(Boolean);
  }
  // Auto-detect
  if (raw === "true") return true;
  if (raw === "false") return false;
  if (!isNaN(Number(raw)) && raw.trim() !== "") return Number(raw);
  return raw;
}

function SuccessBanner({ msg }: { msg: string }) {
  return (
    <div style={{
      background: "var(--success-soft)", border: "1px solid var(--success-border)",
      color: "var(--success)", padding: "10px 16px", borderRadius: "var(--r-md)",
      fontSize: "0.84rem", fontWeight: 600,
    }}>
      ✓ {msg}
    </div>
  );
}
