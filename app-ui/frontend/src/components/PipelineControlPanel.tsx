import { useState, useEffect } from "react";
import { triggerRun, getRuns, getRunDetail } from "../api/client";
import type { PipelineRun, PipelineType } from "../api/types";
import { AppIcon } from "./AppIcon";
import { SectionCard } from "./SectionCard";
import { Modal } from "./Modal";

interface PipelineControlPanelProps {
  agentId: number | null;
  onRefresh?: () => void;
  title?: string;
  global?: boolean;
}

export function PipelineControlPanel({ agentId, onRefresh, title, global = false }: PipelineControlPanelProps) {
  const [runs, setRuns] = useState<PipelineRun[]>([]);
  const [loading, setLoading] = useState(false);
  const [triggering, setTriggering] = useState<PipelineType | null>(null);
  const [selectedRun, setSelectedRun] = useState<PipelineRun | null>(null);
  const [showGlobal, setShowGlobal] = useState(global);
  const [page, setPage] = useState(1);
  const [total, setTotal] = useState(0);
  const [error, setError] = useState("");
  const pageSize = 15;

  const loadRuns = async () => {
    setLoading(true);
    try {
      const targetId = showGlobal ? null : agentId;
      const data = await getRuns(page, pageSize, targetId ?? undefined);
      setRuns(data.items);
      setTotal(data.total);
    } catch (err) {
      console.error("Failed to load runs:", err);
    } finally {
      setLoading(false);
    }
  };

  const runTrigger = async (type: PipelineType) => {
    if (!agentId) return;
    setTriggering(type);
    setError("");
    try {
      await triggerRun(type, { agent_id: agentId });
      await loadRuns();
      if (onRefresh) onRefresh();
    } catch (err) {
      if (err instanceof Error && err.message.includes("not active")) {
        setError("Agent is not active. Go to Agents and set the status to Active before running pipelines.");
      } else {
        setError(err instanceof Error ? err.message : "Trigger failed");
      }
    } finally {
      setTriggering(null);
    }
  };

  const openRun = async (id: number) => {
    try {
      const detail = await getRunDetail(id);
      setSelectedRun(detail);
    } catch (err) {
      console.error("Failed to load run detail:", err);
    }
  };

  useEffect(() => {
    void loadRuns();
    const interval = setInterval(() => void loadRuns(), 5000);
    return () => clearInterval(interval);
  }, [agentId, showGlobal, page]);

  return (
    <div className="pipeline-control-panel">
      <SectionCard title={title || "Pipeline Control Center"} subtitle="Execute strategic intelligence clusters and monitor deterministic execution traces.">
        {error && (
          <div className="error-banner" style={{ marginBottom: "16px" }}>
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" style={{flexShrink:0}}>
              <circle cx="12" cy="12" r="10"/><path d="M12 8v4M12 16h.01"/>
            </svg>
            {error}
          </div>
        )}
        <div className="ops-header-actions" style={{ marginBottom: "16px", display: "flex", justifyContent: "flex-end", gap: "12px", alignItems: "center" }}>
           {agentId && (
              <label className="checkbox-label" style={{ fontSize: "0.8rem", fontWeight: 700, color: "var(--ink-2)", cursor: "pointer", display: "flex", alignItems: "center", gap: "8px" }}>
                 <input type="checkbox" checked={showGlobal} onChange={e => setShowGlobal(e.target.checked)} />
                 Show All Process Runs
              </label>
           )}
           <button className="btn-sm btn-secondary" onClick={loadRuns} disabled={loading}>
              <AppIcon name="clock" size={12} /> {loading ? "Syncing..." : "Refresh"}
           </button>
        </div>
        <div className="ops-layout">
          <div className="ops-triggers">
            <h3 className="ops-label">Triggers</h3>
            <button className="pipeline-btn ingest" disabled={!!triggering} onClick={() => runTrigger("ingest")}>
              <AppIcon name="dashboard" size={14} />
              {triggering === "ingest" ? "Ingesting..." : "Run Ingest"}
            </button>
            <button className="pipeline-btn research" disabled={!!triggering} onClick={() => runTrigger("research")}>
              <AppIcon name="pulse" size={14} />
              {triggering === "research" ? "Researching..." : "Run Research"}
            </button>
            <button className="pipeline-btn intelligence" disabled={!!triggering} onClick={() => runTrigger("intelligence")}>
              <AppIcon name="spark" size={14} />
              {triggering === "intelligence" ? "Analyzing..." : "Run Intelligence"}
            </button>
          </div>

          <div className="ops-telemetry">
             <h3 className="ops-label">Execution Trace Log</h3>
             <div className="telemetry-grid-container">
                <table className="professional-grid">
                   <thead>
                      <tr>
                         <th>Process</th>
                         <th>Status</th>
                         <th>Timestamp</th>
                         <th style={{ textAlign: "right" }}>Logs</th>
                      </tr>
                   </thead>
                   <tbody>
                      {loading && runs.length === 0 ? (
                         <tr><td colSpan={4} style={{ textAlign: "center", padding: "40px" }}>Synchronizing trace...</td></tr>
                      ) : runs.length === 0 ? (
                         <tr><td colSpan={4} style={{ textAlign: "center", padding: "40px", color: "var(--ink-3)" }}>No execution data found.</td></tr>
                      ) : (
                        runs.map((run) => (
                           <tr key={run.id}>
                              <td style={{ fontWeight: 700 }}>{run.pipeline.toUpperCase()}</td>
                              <td><span className={`status-pill-mini ${run.status}`}>{run.status}</span></td>
                             <td style={{ fontSize: "0.75rem", fontFamily: "var(--font-mono)" }}>{run.started_at_utc ? new Date(run.started_at_utc).toLocaleString() : "—"}</td>
                              <td style={{ textAlign: "right" }}>
                                 <button className="btn-sm btn-secondary" onClick={() => openRun(run.id)}>
                                    View Log
                                 </button>
                              </td>
                           </tr>
                         ))
                      )}
                   </tbody>
                </table>
             </div>
             {total > pageSize && (
                <div className="telemetry-pagination" style={{ padding: "12px", borderTop: "1px solid var(--border)", display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                   <span style={{ fontSize: "0.75rem", color: "var(--ink-3)" }}>Showing {Math.min(total, (page-1)*pageSize+1)}-{Math.min(total, page*pageSize)} of {total}</span>
                   <div className="btn-group" style={{ display: "flex", gap: "8px" }}>
                      <button className="btn-sm btn-secondary" disabled={page === 1} onClick={() => setPage(p => p - 1)}>Prev</button>
                      <button className="btn-sm btn-secondary" disabled={page * pageSize >= total} onClick={() => setPage(p => p + 1)}>Next</button>
                   </div>
                </div>
             )}
          </div>
        </div>
      </SectionCard>

      <Modal open={!!selectedRun} onClose={() => setSelectedRun(null)} title="System Trace Output" size="xl">
        {selectedRun && (
          <div className="trace-modal">
             <div className="trace-header">
                <div className="trace-meta">
                   <div className={`trace-badge ${selectedRun.status}`}>{selectedRun.status.toUpperCase()}</div>
                   <div className="trace-title">{selectedRun.pipeline.replace("_", " ").toUpperCase()} CLUSTER</div>
                   <div className="trace-id">RUN_ID: {selectedRun.id}</div>
                </div>
                <div className="trace-time">INITIATED: {selectedRun.started_at_utc}</div>
             </div>
             <div className="trace-body">
                <pre className="trace-logs">
                  {selectedRun.log_text || selectedRun.message || "Log stream exhausted or no output captured."}
                </pre>
             </div>
          </div>
        )}
      </Modal>

      <style>{`
        .ops-layout { display: grid; grid-template-columns: 240px 1fr; gap: 32px; padding: 12px; }
        .ops-triggers { display: flex; flex-direction: column; gap: 12px; }
        .ops-label { font-size: 0.75rem; font-weight: 800; color: var(--ink-2); text-transform: uppercase; letter-spacing: 0.1em; margin-bottom: 8px; }
        
        .ops-triggers .pipeline-btn { 
          width: 100%; 
          justify-content: flex-start; 
          padding: 14px 20px; 
          font-weight: 700;
          font-size: 0.9rem;
          border-radius: 12px;
        }
        
        .ops-telemetry { min-width: 0; }
        .telemetry-grid-container { 
          max-height: 400px; 
          overflow-y: auto; 
          border: 1px solid var(--border);
          border-radius: 12px;
          background: var(--bg-page);
        }
        
        .status-pill-mini {
           font-size: 0.65rem;
           font-weight: 800;
           padding: 2px 8px;
           border-radius: 4px;
           text-transform: uppercase;
        }
        .status-pill-mini.completed { background: var(--success-soft); color: var(--success); }
        .status-pill-mini.running { background: var(--blue-soft); color: var(--blue); }
        .status-pill-mini.failed { background: var(--danger-soft); color: var(--danger); }
        
        .trace-modal { background: #080808; border-radius: 12px; overflow: hidden; display: flex; flex-direction: column; max-height: 80vh; }
        .trace-header { padding: 20px 24px; background: #111; border-bottom: 1px solid #222; display: flex; justify-content: space-between; align-items: center; }
        .trace-meta { display: flex; align-items: center; gap: 16px; }
        .trace-badge { padding: 4px 12px; border-radius: 6px; font-weight: 800; font-size: 0.75rem; }
        .trace-badge.completed { background: #065f46; color: #34d399; }
        .trace-badge.running { background: #1e3a8a; color: #60a5fa; }
        .trace-badge.failed { background: #7f1d1d; color: #f87171; }
        
        .trace-title { font-weight: 800; font-size: 1rem; color: #fff; letter-spacing: -0.02em; }
        .trace-id { color: #555; font-family: var(--font-mono); font-size: 0.75rem; }
        .trace-time { color: #888; font-size: 0.75rem; font-weight: 600; }
        
        .trace-body { padding: 0; flex: 1; overflow: auto; background: #000; }
        .trace-logs { 
          margin: 0;
          padding: 24px; 
          font-family: 'DM Mono', 'Fira Code', monospace; 
          font-size: 0.85rem; 
          line-height: 1.6;           
          color: #a5f3fc; 
          white-space: pre-wrap; 
          word-break: break-all;
          background: #000;
          display: block;
          width: 100%;
          min-height: 300px;
        }
        
        @media (max-width: 900px) {
          .ops-layout { grid-template-columns: 1fr; }
        }
      `}</style>
    </div>
  );
}
