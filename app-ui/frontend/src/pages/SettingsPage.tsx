import { FormEvent, useEffect, useMemo, useState } from "react";
import { getSettings, upsertSetting, getKeywords, createKeyword, updateKeyword, deleteKeyword } from "../api/client";
import type { SettingRow, KeywordRow } from "../api/types";
import { Modal } from "../components/Modal";
import { AppIcon } from "../components/AppIcon";
import { Pagination } from "../components/Pagination";

const CATEGORY_ORDER = ["database", "browser", "llm", "llm_fallback", "research", "outreach", "email", "general"];
const CATEGORY_LABELS: Record<string, string> = {
  database: "Database", browser: "Browser", llm: "LLM", llm_fallback: "LLM Fallback",
  research: "Research", outreach: "Outreach", email: "Email / SMTP", general: "General",
};
const LLM_FALLBACK_KEYS = new Set(["LLM_FALLBACK_ENABLED", "LLM_FALLBACK_PROVIDER", "LLM_FALLBACK_MODEL"]);

function normalizeType(v: string | undefined | null): string {
  const s = String(v || "").trim().toLowerCase(); return s || "general";
}
function inferTypeFromKey(key: string): string {
  const n = String(key || "").trim().toUpperCase();
  if (n.startsWith("DB_")) return "database";
  if (n.startsWith("BROWSER_") || n.startsWith("CHROME_")) return "browser";
  if (n.startsWith("LLM_")) return LLM_FALLBACK_KEYS.has(n) ? "llm_fallback" : "llm";
  if (n.startsWith("RESEARCH_")) return "research";
  if (n.startsWith("OUTREACH_")) return "outreach";
  if (n.startsWith("SMTP_")) return "email";
  return "general";
}
function isPasswordSetting(key: string) { return String(key || "").toUpperCase().includes("PASSWORD"); }
function displayValue(row: SettingRow): string {
  if (isPasswordSetting(row.setting_key)) return "••••••••";
  const v = row.setting_value;
  if (v == null) return "—";
  if (typeof v === "object") { try { return JSON.stringify(v); } catch { return String(v); } }
  return String(v);
}

const EMPTY_KW: Omit<KeywordRow, "id" | "last_run_utc" | "created_at_utc" | "updated_at_utc"> = {
  keyword: "", location: "", max_job_age_days: 7, max_jobs: 50, active: true,
};

export function SettingsPage() {
  // ── App settings ──────────────────────────────────────────────────────
  const [settings, setSettings] = useState<SettingRow[]>([]);
  const [loadingSettings, setLoadingSettings] = useState(false);
  const [error, setError] = useState("");
  const [editRow, setEditRow] = useState<SettingRow | null>(null);
  const [editValue, setEditValue] = useState("");

  // ── Ingestion configs ─────────────────────────────────────────────────
  const [kwRows, setKwRows] = useState<KeywordRow[]>([]);
  const [kwTotal, setKwTotal] = useState(0);
  const [kwPage, setKwPage] = useState(1);
  const KW_PAGE_SIZE = 20;
  const [loadingKw, setLoadingKw] = useState(false);
  const [kwError, setKwError] = useState("");
  const [kwModal, setKwModal] = useState<{ mode: "create" | "edit"; row: Partial<KeywordRow> } | null>(null);
  const [kwSaving, setKwSaving] = useState(false);
  const [kwDeleteId, setKwDeleteId] = useState<number | null>(null);

  const loadSettings = async () => {
    setLoadingSettings(true); setError("");
    try { const r = await getSettings(); setSettings(r.items); }
    catch (err) { setError(err instanceof Error ? err.message : "Failed to load settings"); }
    finally { setLoadingSettings(false); }
  };

  const loadKeywords = async (p = kwPage) => {
    setLoadingKw(true); setKwError("");
    try {
      const r = await getKeywords(p, KW_PAGE_SIZE);
      setKwRows(r.items); setKwTotal(r.total); setKwPage(p);
    } catch (err) { setKwError(err instanceof Error ? err.message : "Failed to load ingestion configs"); }
    finally { setLoadingKw(false); }
  };

  useEffect(() => { void loadSettings(); void loadKeywords(1); }, []);

  const grouped = useMemo(() => {
    const groups: Record<string, SettingRow[]> = {};
    for (const s of settings) {
      const cat = LLM_FALLBACK_KEYS.has(s.setting_key)
        ? "llm_fallback"
        : normalizeType(s.config_type) || inferTypeFromKey(s.setting_key);
      if (!groups[cat]) groups[cat] = [];
      groups[cat].push(s);
    }
    return groups;
  }, [settings]);

  const orderedCategories = useMemo(() => {
    const keys = Object.keys(grouped);
    return [...CATEGORY_ORDER.filter((k) => keys.includes(k)), ...keys.filter((k) => !CATEGORY_ORDER.includes(k))];
  }, [grouped]);

  const openEdit = (row: SettingRow) => {
    setEditRow(row);
    const v = row.setting_value;
    setEditValue(v == null ? "" : typeof v === "object" ? JSON.stringify(v, null, 2) : String(v));
  };

  const saveEdit = async (e: FormEvent) => {
    e.preventDefault();
    if (!editRow) return;
    try {
      let val: unknown = editValue;
      try { val = JSON.parse(editValue); } catch { /* keep as string */ }
      await upsertSetting(editRow.setting_key, val);
      setEditRow(null);
      await loadSettings();
    } catch (err) { setError(err instanceof Error ? err.message : "Save failed"); }
  };

  // ── Ingestion CRUD ────────────────────────────────────────────────────
  const openKwCreate = () => setKwModal({ mode: "create", row: { ...EMPTY_KW } });
  const openKwEdit = (r: KeywordRow) => setKwModal({ mode: "edit", row: { ...r } });

  const saveKw = async (e: FormEvent) => {
    e.preventDefault();
    if (!kwModal) return;
    const row = kwModal.row;
    if (!String(row.keyword || "").trim()) return;
    setKwSaving(true);
    try {
      const payload = {
        keyword: String(row.keyword || "").trim(),
        location: String(row.location || "").trim() || undefined,
        max_job_age_days: Number(row.max_job_age_days) || 7,
        max_jobs: Number(row.max_jobs) || 50,
        active: !!row.active,
      };
      if (kwModal.mode === "create") {
        await createKeyword(payload);
      } else {
        await updateKeyword(row.id!, payload);
      }
      setKwModal(null);
      await loadKeywords(kwModal.mode === "create" ? 1 : kwPage);
    } catch (err) { setKwError(err instanceof Error ? err.message : "Save failed"); }
    finally { setKwSaving(false); }
  };

  const confirmDelete = async () => {
    if (kwDeleteId == null) return;
    try {
      await deleteKeyword(kwDeleteId);
      setKwDeleteId(null);
      await loadKeywords(1);
    } catch (err) { setKwError(err instanceof Error ? err.message : "Delete failed"); }
  };

  const kwFormRow = kwModal?.row ?? {};

  return (
    <div className="page-stack">
      <div className="page-header fade-in">
        <div>
          <h1>Settings</h1>
          <p className="page-sub">Manage application configuration and job ingestion rules.</p>
        </div>
        <div className="page-header-actions">
          <button className="btn-secondary" onClick={() => { void loadSettings(); void loadKeywords(1); }} disabled={loadingSettings || loadingKw} style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
            {(loadingSettings || loadingKw) ? <><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" className="spin"><path d="M21 12a9 9 0 1 1-6.219-8.56" /></svg> Loading</> : "Refresh"}
          </button>
        </div>
      </div>

      {error && (
        <div className="error-banner">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" style={{ flexShrink: 0 }}><circle cx="12" cy="12" r="10" /><path d="M12 8v4M12 16h.01" /></svg>
          {error}
        </div>
      )}

      {/* ── Job Ingestion Configs CRUD grid ─────────────────────────── */}
      <div className="intelligence-grid-wrap fade-in">
        <div className="grid-toolbar">
          <h3 style={{ margin: 0, fontSize: "0.9rem" }}>Job Ingestion Configs</h3>
          <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <span className="muted" style={{ fontSize: "0.75rem" }}>{kwTotal} configs</span>
            <button className="btn-primary btn-sm" onClick={openKwCreate} style={{ display: "inline-flex", alignItems: "center", gap: 5 }}>
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round"><path d="M12 5v14M5 12h14" /></svg>
              Add Config
            </button>
          </div>
        </div>

        {kwError && (
          <div className="error-banner" style={{ margin: "0 0 8px" }}>
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" style={{ flexShrink: 0 }}><circle cx="12" cy="12" r="10" /><path d="M12 8v4M12 16h.01" /></svg>
            {kwError}
          </div>
        )}

        <table className="professional-grid">
          <thead>
            <tr>
              <th>Job Title / Keyword</th>
              <th>Location</th>
              <th style={{ width: 110 }}>Max Age (days)</th>
              <th style={{ width: 110 }}>Max Records</th>
              <th style={{ width: 80 }}>Active</th>
              <th style={{ width: 140 }}>Last Run</th>
              <th style={{ width: 80 }}></th>
            </tr>
          </thead>
          <tbody>
            {loadingKw && (
              <tr><td colSpan={7} style={{ textAlign: "center", padding: "18px 0", color: "var(--ink-3)" }}>Loading…</td></tr>
            )}
            {!loadingKw && kwRows.length === 0 && (
              <tr><td colSpan={7} style={{ textAlign: "center", padding: "18px 0", color: "var(--ink-3)" }}>
                No ingestion configs yet. Click <strong>Add Config</strong> to create one.
              </td></tr>
            )}
            {!loadingKw && kwRows.map((r) => (
              <tr key={r.id}>
                <td style={{ fontWeight: 600 }}>{r.keyword}</td>
                <td style={{ color: "var(--ink-2)" }}>{r.location || <span className="muted">—</span>}</td>
                <td style={{ textAlign: "center" }}>{r.max_job_age_days}</td>
                <td style={{ textAlign: "center" }}>{r.max_jobs}</td>
                <td style={{ textAlign: "center" }}>
                  <span className={`status-badge ${r.active ? "status-active" : "status-paused"}`}>
                    {r.active ? "Yes" : "No"}
                  </span>
                </td>
                <td style={{ fontSize: "0.75rem", color: "var(--ink-3)" }}>
                  {r.last_run_utc ? new Date(r.last_run_utc).toLocaleString() : "—"}
                </td>
                <td>
                  <div style={{ display: "flex", gap: 4 }}>
                    <button className="row-action-btn" onClick={() => openKwEdit(r)} title="Edit"><AppIcon name="settings" size={14} /></button>
                    <button className="row-action-btn" onClick={() => setKwDeleteId(r.id)} title="Delete" style={{ color: "var(--red)" }}>
                      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><path d="M3 6h18M19 6l-1 14H6L5 6M10 11v6M14 11v6M9 6V4h6v2" /></svg>
                    </button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        <Pagination page={kwPage} pageSize={KW_PAGE_SIZE} totalItems={kwTotal} onPageChange={(p) => loadKeywords(p)} />
      </div>

      {/* ── App settings sections ────────────────────────────────────── */}
      {orderedCategories.map((cat) => (
        <div key={cat} className="intelligence-grid-wrap fade-in">
          <div className="grid-toolbar">
            <h3 style={{ margin: 0, fontSize: "0.9rem" }}>{CATEGORY_LABELS[cat] ?? cat}</h3>
            <span className="muted" style={{ fontSize: "0.75rem" }}>{grouped[cat]?.length ?? 0} Keys</span>
          </div>
          <table className="professional-grid">
            <thead>
              <tr>
                <th>Configuration Key</th>
                <th>Active Value</th>
                <th>Context / Description</th>
                <th style={{ width: "60px" }}></th>
              </tr>
            </thead>
            <tbody>
              {(grouped[cat] ?? []).map((row) => (
                <tr key={row.setting_key}>
                  <td style={{ fontFamily: "var(--font-mono)", fontSize: "0.75rem", fontWeight: 700 }}>{row.setting_key}</td>
                  <td style={{ fontFamily: "var(--font-mono)", fontSize: "0.75rem", color: "var(--blue)" }}>{displayValue(row)}</td>
                  <td style={{ fontSize: "0.8rem", color: "var(--ink-2)" }}>{row.description ?? "—"}</td>
                  <td>
                    <button className="row-action-btn" onClick={() => openEdit(row)} title="Update value">
                      <AppIcon name="settings" size={14} />
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ))}

      {/* ── Edit setting modal ───────────────────────────────────────── */}
      <Modal open={!!editRow} onClose={() => setEditRow(null)} title={`Edit: ${editRow?.setting_key}`} subtitle={editRow?.description} size="md">
        {editRow && (
          <form onSubmit={saveEdit} style={{ display: "flex", flexDirection: "column", gap: 14 }}>
            <label>Value
              <textarea value={editValue} onChange={(e) => setEditValue(e.target.value)} style={{ minHeight: 120 }} />
            </label>
            <div style={{ display: "flex", gap: 8 }}>
              <button className="btn-primary btn-sm" type="submit">Save</button>
              <button className="btn-ghost btn-sm" type="button" onClick={() => setEditRow(null)}>Cancel</button>
            </div>
          </form>
        )}
      </Modal>

      {/* ── Ingestion config create/edit modal ──────────────────────── */}
      <Modal
        open={!!kwModal}
        onClose={() => setKwModal(null)}
        title={kwModal?.mode === "create" ? "Add Ingestion Config" : "Edit Ingestion Config"}
        subtitle="Defines one keyword + location combination to scrape jobs for."
        size="md"
      >
        {kwModal && (
          <form onSubmit={saveKw} style={{ display: "flex", flexDirection: "column", gap: 14 }}>
            <label>Job Title / Keyword *
              <input
                type="text"
                value={kwFormRow.keyword ?? ""}
                onChange={(e) => setKwModal((m) => m ? { ...m, row: { ...m.row, keyword: e.target.value } } : m)}
                placeholder="e.g. AI Engineer"
                required
              />
            </label>
            <label>Location
              <input
                type="text"
                value={kwFormRow.location ?? ""}
                onChange={(e) => setKwModal((m) => m ? { ...m, row: { ...m.row, location: e.target.value } } : m)}
                placeholder="e.g. Bangalore (leave blank for any)"
              />
            </label>
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
              <label>Max Age (days)
                <input
                  type="number"
                  min={1} max={365}
                  value={kwFormRow.max_job_age_days ?? 7}
                  onChange={(e) => setKwModal((m) => m ? { ...m, row: { ...m.row, max_job_age_days: Number(e.target.value) } } : m)}
                />
              </label>
              <label>Max Records to Fetch
                <input
                  type="number"
                  min={1} max={5000}
                  value={kwFormRow.max_jobs ?? 50}
                  onChange={(e) => setKwModal((m) => m ? { ...m, row: { ...m.row, max_jobs: Number(e.target.value) } } : m)}
                />
              </label>
            </div>
            <label style={{ flexDirection: "row", alignItems: "center", gap: 10, cursor: "pointer" }}>
              <input
                type="checkbox"
                checked={!!kwFormRow.active}
                onChange={(e) => setKwModal((m) => m ? { ...m, row: { ...m.row, active: e.target.checked } } : m)}
                style={{ width: "auto" }}
              />
              Active (include in ingestion runs)
            </label>
            <div style={{ display: "flex", gap: 8 }}>
              <button className="btn-primary btn-sm" type="submit" disabled={kwSaving}>
                {kwSaving ? "Saving…" : kwModal.mode === "create" ? "Create" : "Save Changes"}
              </button>
              <button className="btn-ghost btn-sm" type="button" onClick={() => setKwModal(null)}>Cancel</button>
            </div>
          </form>
        )}
      </Modal>

      {/* ── Delete confirmation modal ────────────────────────────────── */}
      <Modal open={kwDeleteId != null} onClose={() => setKwDeleteId(null)} title="Delete Ingestion Config" size="md">
        <p style={{ marginBottom: 16 }}>This ingestion config will be permanently removed. Jobs already fetched are not affected.</p>
        <div style={{ display: "flex", gap: 8 }}>
          <button className="btn-primary btn-sm" style={{ background: "var(--red)" }} onClick={() => void confirmDelete()}>Delete</button>
          <button className="btn-ghost btn-sm" onClick={() => setKwDeleteId(null)}>Cancel</button>
        </div>
      </Modal>
    </div>
  );
}