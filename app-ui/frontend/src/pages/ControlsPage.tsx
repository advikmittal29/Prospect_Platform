import { FormEvent, useEffect, useState } from "react";
import { useOutletContext } from "react-router-dom";
import {
  createKeyword,
  deleteKeyword,
  getKeywords,
  getLinkedinCredentials,
  updateKeyword,
  upsertLinkedinCredentials,
} from "../api/client";
import type { KeywordRow, LinkedInCredentialRow } from "../api/types";
import type { AgentScopeContextValue } from "../agentScope";
import { SectionCard } from "../components/SectionCard";
import { AppIcon } from "../components/AppIcon";
import { PipelineControlPanel } from "../components/PipelineControlPanel";

const initKw = { id: 0, keyword: "", location: "", max_job_age_days: 7, max_jobs: 50, active: true };
const initCred = { email: "", password: "", priority: 100, active: true };

export function ControlsPage() {
  const { activeAgentId, agentScopeVersion } = useOutletContext<AgentScopeContextValue>();
  const [keywords, setKeywords] = useState<KeywordRow[]>([]);
  const [kwForm, setKwForm] = useState(initKw);
  const [credentials, setCredentials] = useState<LinkedInCredentialRow[]>([]);
  const [credForm, setCredForm] = useState(initCred);
  const [_busy, setBusy] = useState(false);

  const loadData = async () => {
    setBusy(true);
    try {
      const [k, c] = await Promise.all([getKeywords(), getLinkedinCredentials()]);
      setKeywords(k.items);
      setCredentials(c.items);
    } catch (err) {
      console.error("Load failed", err);
    } finally {
      setBusy(false);
    }
  };

  useEffect(() => {
    void loadData();
  }, [agentScopeVersion]);

  const submitKw = async (e: FormEvent) => {
    e.preventDefault();
    try {
      const payload = {
        keyword: kwForm.keyword,
        location: kwForm.location || undefined,
        max_job_age_days: Number(kwForm.max_job_age_days),
        max_jobs: Number(kwForm.max_jobs),
        active: kwForm.active,
      };
      if (kwForm.id > 0) await updateKeyword(kwForm.id, payload);
      else await createKeyword(payload);
      setKwForm(initKw);
      await loadData();
    } catch (err) {
      console.error("Save failed", err);
    }
  };

  const submitCred = async (e: FormEvent) => {
    e.preventDefault();
    try {
      await upsertLinkedinCredentials({
        email: credForm.email,
        password: credForm.password,
        priority: Number(credForm.priority),
        active: credForm.active,
      });
      setCredForm(initCred);
      await loadData();
    } catch (err) {
      console.error("Save failed", err);
    }
  };

  return (
    <div className="page-stack fade-in">
      <div className="page-header">
        <div>
          <h1>Global Control Center</h1>
          <p className="page-sub">Comprehensive orchestration and system overrides for the multi-agent cluster.</p>
        </div>
      </div>

      <PipelineControlPanel 
        agentId={activeAgentId} 
        title="Global System Operations" 
        global={true} 
      />

      <div className="dashboard-grid">
         <div className="grid-main">
            <SectionCard title="Ingestion Strategies" subtitle="Active search and target acquisition parameters" noPad>
               <div style={{ padding: "20px", borderBottom: "1px solid var(--border)", background: "var(--bg-subtle)" }}>
                  <form className="form-grid" onSubmit={submitKw} style={{ alignItems: "flex-end" }}>
                     <label>
                        Keyword Strategy
                        <input value={kwForm.keyword} onChange={(e) => setKwForm((p) => ({ ...p, keyword: e.target.value }))} required placeholder="AI Engineer" />
                     </label>
                     <label>
                        Location Scope
                        <input value={kwForm.location} onChange={(e) => setKwForm((p) => ({ ...p, location: e.target.value }))} placeholder="Remote" />
                     </label>
                     <label>
                        Max Job Results
                        <input type="number" value={kwForm.max_jobs} onChange={(e) => setKwForm((p) => ({ ...p, max_jobs: Number(e.target.value) }))} />
                     </label>
                     <div className="form-actions" style={{ display: "flex", gap: "8px" }}>
                        <button className="btn-primary" type="submit">{kwForm.id > 0 ? "Update" : "Add Strategy"}</button>
                        {kwForm.id > 0 && <button className="btn-secondary" type="button" onClick={() => setKwForm(initKw)}>Cancel</button>}
                     </div>
                  </form>
               </div>
               <table className="professional-grid">
                  <thead>
                     <tr>
                        <th>Keyword Strategy</th>
                        <th>Location Scope</th>
                        <th>Status</th>
                        <th>Actions</th>
                     </tr>
                  </thead>
                  <tbody>
                     {keywords.map((row) => (
                        <tr key={row.id}>
                           <td style={{ fontWeight: 700 }}>{row.keyword}</td>
                           <td>{row.location ?? "Global"}</td>
                           <td><span className={`status-tag ${row.active ? "completed" : "failed"}`}>{row.active ? "active" : "paused"}</span></td>
                           <td>
                              <div className="btn-row" style={{ gap: "8px" }}>
                                 <button className="row-action-btn" onClick={() => setKwForm({ id: row.id, keyword: row.keyword, location: row.location ?? "", max_job_age_days: row.max_job_age_days, max_jobs: row.max_jobs, active: row.active })}><AppIcon name="settings" size={14} /></button>
                                 <button className="row-action-btn" style={{ color: "var(--danger)" }} onClick={() => void deleteKeyword(row.id).then(loadData)}><AppIcon name="close" size={14} /></button>
                              </div>
                           </td>
                        </tr>
                     ))}
                     {keywords.length === 0 && (
                        <tr><td colSpan={4} style={{ textAlign: "center", padding: "40px", color: "var(--ink-3)" }}>No ingestion strategies defined.</td></tr>
                     )}
                  </tbody>
               </table>
            </SectionCard>
         </div>

         <div className="grid-side">
            <SectionCard title="System Credentials" subtitle="LinkedIn Access Cluster">
               <div style={{ marginBottom: "20px" }}>
                  <form className="form-grid" onSubmit={submitCred} style={{ gridTemplateColumns: "1fr" }}>
                     <label>
                        Account Email
                        <input value={credForm.email} onChange={(e) => setCredForm((p) => ({ ...p, email: e.target.value }))} required />
                     </label>
                     <label>
                        Password
                        <input type="password" value={credForm.password} onChange={(e) => setCredForm((p) => ({ ...p, password: e.target.value }))} required />
                     </label>
                     <button className="btn-primary" type="submit">Deploy Credential</button>
                  </form>
               </div>
               <div className="agent-list-mini">
                  {credentials.map(c => (
                     <div key={c.email} className="agent-item-row" style={{ padding: "12px" }}>
                        <div className="agent-info">
                           <span className="agent-name" style={{ fontSize: "0.85rem" }}>{c.email}</span>
                           <span className="agent-type">Priority: {c.priority}</span>
                        </div>
                        <span className={`status-tag ${c.active ? 'completed' : 'failed'}`} style={{ fontSize: "0.6rem" }}>{c.active ? 'Active' : 'Locked'}</span>
                     </div>
                  ))}
               </div>
            </SectionCard>
         </div>
      </div>

      <style>{`
        .page-stack { display: flex; flex-direction: column; gap: 24px; }
        .form-grid { display: grid; gap: 16px; }
        .form-grid label { display: flex; flex-direction: column; gap: 8px; font-size: 0.75rem; font-weight: 800; color: var(--ink-2); text-transform: uppercase; }
        .form-grid input { padding: 10px 14px; border: 1px solid var(--border); border-radius: 8px; background: var(--bg-card); color: var(--ink-0); }
      `}</style>
    </div>
  );
}
