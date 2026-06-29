import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { getAgents, getDashboardSummary } from "../api/client";
import type { AgentDefinitionRow, DashboardSummary, PipelineRun } from "../api/types";
import { Modal } from "../components/Modal";
import { SectionCard } from "../components/SectionCard";
import { AppIcon } from "../components/AppIcon";

const pct = (p: number, t: number) => (!t ? 0 : (p / t) * 100);

const Spinner = () => (
  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" className="spin">
    <path d="M21 12a9 9 0 1 1-6.219-8.56" />
  </svg>
);

export function DashboardPage() {
  const [agents, setAgents] = useState<AgentDefinitionRow[]>([]);
  const [summary, setSummary] = useState<DashboardSummary | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [selectedRun, setSelectedRun] = useState<PipelineRun | null>(null);

  const load = async () => {
    setError("");
    setLoading(true);
    try {
      const [dashSummary, agentsResp] = await Promise.all([
        getDashboardSummary(null),
        getAgents()
      ]);
      setSummary(dashSummary);
      setAgents(agentsResp.items ?? []);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load dashboard");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void load();
  }, []);

  const metrics = summary?.metrics ?? {};
  const recentRuns = summary?.recent_runs ?? [];

  const stats = [
    { label: "Active Agents", value: agents.filter(a => a.status === "active").length, icon: "agents", color: "blue", trend: "Fleet operational" },
    { label: "Companies Identified", value: metrics.companies_total || 0, icon: "companies", color: "violet", trend: `${metrics.companies_completed || 0} researched` },
    { label: "Prospect Intelligence", value: metrics.prospects_total || 0, icon: "prospects", color: "teal", trend: `${metrics.prospects_hot || 0} high-intent` },
    { label: "Pipeline Integrity", value: `${recentRuns.length ? Math.round(pct(recentRuns.filter(r => r.status === "completed").length, recentRuns.length)) : 100}%`, icon: "check", color: "emerald", trend: "Success rate" },
  ];

  return (
    <div className="page-stack fade-in">
      <div className="page-header">
        <div>
          <h1>Control Room Overview</h1>
          <p className="page-sub">Global orchestration and system-wide pipeline intelligence.</p>
        </div>
        <div className="page-header-actions">
          <button className="btn-secondary" onClick={() => void load()} disabled={loading}>
            {loading ? <Spinner /> : <AppIcon name="clock" size={14} />} Refresh
          </button>
        </div>
      </div>

      {error && <div className="error-banner">{error}</div>}

      <div className="kpi-row" style={{ marginBottom: "24px" }}>
        {stats.map((stat, i) => (
          <div key={i} className={`kpi-card-premium fade-in stagger-${i+1}`}>
            <div className={`kpi-icon-wrap ${stat.color}`}>
              <AppIcon name={stat.icon as any} size={20} />
            </div>
            <div className="kpi-body">
              <span className="kpi-label">{stat.label}</span>
              <span className="kpi-value">{stat.value}</span>
              <div className="kpi-trend-box">
                <span className={`kpi-trend-val ${stat.trend.startsWith('+') ? 'up' : 'down'}`}>
                   {stat.trend}
                </span>
                <span className="kpi-trend-label">since yesterday</span>
              </div>
            </div>
          </div>
        ))}
      </div>

      <div className="dashboard-main-grid">
        <div className="grid-left">
          <SectionCard title="System-wide Execution Stream" subtitle="Live pipeline telemetry and orchestration logs" noPad>
            <div className="telemetry-list">
              {recentRuns.length === 0 ? (
                <div className="empty-msg" style={{ padding: "60px 40px", textAlign: "center" }}>
                   <div style={{ background: "var(--bg-subtle)", width: "64px", height: "64px", borderRadius: "50%", display: "flex", alignItems: "center", justifyContent: "center", margin: "0 auto 16px" }}>
                      <AppIcon name="clock" size={32} style={{ opacity: 0.3 }} />
                   </div>
                  <p style={{ fontWeight: 600, color: "var(--ink-1)" }}>No execution data available</p>
                  <p style={{ fontSize: "0.85rem", color: "var(--ink-2)", marginTop: "4px" }}>The pipeline is currently idle or waiting for scheduled tasks.</p>
                </div>
              ) : (
                recentRuns.slice(0, 15).map((run) => (
                  <div key={run.id} className="telemetry-item-premium" onClick={() => setSelectedRun(run)}>
                    <div className={`tel-status-bar ${run.status}`}></div>
                    <div className="tel-icon-side">
                        <div className={`tel-mini-icon ${run.status}`}>
                           <AppIcon name={run.status === 'completed' ? 'dashboard' : run.status === 'failed' ? 'close' : 'clock'} size={12} />
                        </div>
                    </div>
                    <div className="tel-main">
                      <div className="tel-row-top">
                        <span className="tel-pipeline-name">{run.pipeline}</span>
                        <span className="tel-timestamp">{run.started_at_utc ? new Date(run.started_at_utc).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' }) : "-"}</span>
                      </div>
                      <div className="tel-row-bottom">
                        <div className="tel-meta-badges">
                           <span className="tel-badge-agent">
                              <AppIcon name="dashboard" size={10} style={{ marginRight: "4px", opacity: 0.6 }} />
                              {agents.find(a => a.id === run.agent_id)?.name || "System Orchestrator"}
                           </span>
                           {run.message && (
                             <span className="tel-msg-preview">{run.message}</span>
                           )}
                        </div>
                        <span className={`status-tag ${run.status}`}>{run.status}</span>
                      </div>
                    </div>
                  </div>
                ))
              )}
            </div>
          </SectionCard>
        </div>

        <div className="grid-right">
          <SectionCard title="Active Agent Fleet" subtitle="Connected autonomous entities">
            <div className="agent-list-mini">
              {agents.length === 0 ? (
                <p className="empty-msg">No agents initialized.</p>
              ) : (
                agents.map((agent) => (
                  <Link key={agent.id} to={`/agent/${agent.id}/dashboard`} className="agent-item-row" style={{ border: "1px solid var(--border)", borderRadius: "var(--r-md)", marginBottom: "8px" }}>
                    <div className="agent-avatar-wrap">
                      <div className="agent-avatar" style={{ background: agent.status === 'active' ? 'var(--blue-soft)' : 'var(--bg-subtle)' }}>{agent.name[0]}</div>
                    </div>
                    <div className="agent-info">
                      <span className="agent-name">{agent.name}</span>
                      <span className="agent-type" style={{ fontSize: "0.7rem" }}>{agent.agent_type}</span>
                    </div>
                    <div className={`status-pill ${agent.status}`} style={{ fontSize: "0.6rem", padding: "2px 6px" }}>{agent.status}</div>
                  </Link>
                ))
              )}
            </div>
          </SectionCard>
          
          <SectionCard title="System Information">
             <div className="system-info-list" style={{ display: "flex", flexDirection: "column", gap: "12px" }}>
                <div className="info-item" style={{ display: "flex", justifyContent: "space-between", fontSize: "0.85rem" }}>
                  <span style={{ color: "var(--ink-2)" }}>Kernel Version</span>
                  <span style={{ fontWeight: 600 }}>v2.4.0-stable</span>
                </div>
                <div className="info-item" style={{ display: "flex", justifyContent: "space-between", fontSize: "0.85rem" }}>
                  <span style={{ color: "var(--ink-2)" }}>Orchestration</span>
                  <span style={{ fontWeight: 600 }}>Deterministic</span>
                </div>
                <div className="info-item" style={{ display: "flex", justifyContent: "space-between", fontSize: "0.85rem" }}>
                  <span style={{ color: "var(--ink-2)" }}>Last Sync</span>
                  <span style={{ fontWeight: 600 }}>Just now</span>
                </div>
             </div>
          </SectionCard>
        </div>
      </div>

      <Modal open={!!selectedRun} onClose={() => setSelectedRun(null)} title={`Execution Detail #${selectedRun?.id}`} size="lg">
        {selectedRun && (
          <div className="run-detail">
            <div className="detail-meta">
              <div className="meta-item">
                <label>Status</label>
                <span className={`status-pill ${selectedRun.status}`}>{selectedRun.status}</span>
              </div>
              <div className="meta-item">
                <label>Duration</label>
                <span>{selectedRun.ended_at_utc ? 'Completed' : 'Running...'}</span>
              </div>
            </div>
            <div className="log-area">
              <label>Execution Log</label>
              <pre>{selectedRun.log_text || selectedRun.message || "No logs available"}</pre>
            </div>
          </div>
        )}
      </Modal>

      <style>{`
        .kpi-row {
          display: grid;
          grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
          gap: 20px;
        }

        .kpi-card-premium {
          background: var(--surface);
          border: 1px solid var(--border);
          border-radius: var(--r-xl);
          padding: 24px;
          display: flex;
          gap: 20px;
          box-shadow: var(--shadow-sm);
          transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
          position: relative;
          overflow: hidden;
        }

        .kpi-card-premium:hover {
          transform: translateY(-4px);
          box-shadow: var(--shadow-lg);
          border-color: var(--blue-border);
        }

        .kpi-icon-wrap {
          width: 52px;
          height: 52px;
          border-radius: 14px;
          display: flex;
          align-items: center;
          justify-content: center;
          color: white;
          flex-shrink: 0;
        }

        .kpi-icon-wrap.blue { background: linear-gradient(135deg, #3b82f6, #2563eb); box-shadow: 0 4px 12px rgba(37, 99, 235, 0.3); }
        .kpi-icon-wrap.violet { background: linear-gradient(135deg, #8b5cf6, #7c3aed); box-shadow: 0 4px 12px rgba(124, 58, 237, 0.3); }
        .kpi-icon-wrap.teal { background: linear-gradient(135deg, #14b8a6, #0d9488); box-shadow: 0 4px 12px rgba(20, 184, 166, 0.3); }
        .kpi-icon-wrap.emerald { background: linear-gradient(135deg, #10b981, #059669); box-shadow: 0 4px 12px rgba(16, 185, 129, 0.3); }

        .kpi-body {
          display: flex;
          flex-direction: column;
          justify-content: center;
        }

        .kpi-label {
          font-size: 0.72rem;
          font-weight: 700;
          color: var(--ink-2);
          text-transform: uppercase;
          letter-spacing: 0.08em;
          margin-bottom: 2px;
        }

        .kpi-value {
          font-size: 2rem;
          font-weight: 800;
          color: var(--ink-0);
          letter-spacing: -0.04em;
          line-height: 1;
          margin: 4px 0;
        }

        .kpi-trend-box {
          display: flex;
          align-items: center;
          gap: 6px;
          margin-top: 4px;
        }

        .kpi-trend-val {
          font-size: 0.75rem;
          font-weight: 700;
          padding: 2px 6px;
          border-radius: 4px;
        }

        .kpi-trend-val.up { color: var(--success); background: var(--success-soft); }
        .kpi-trend-val.down { color: var(--danger); background: var(--danger-soft); }

        .kpi-trend-label {
          font-size: 0.72rem;
          color: var(--ink-3);
          font-weight: 500;
        }

        .dashboard-main-grid {
          display: grid;
          grid-template-columns: 1fr 360px;
          gap: 24px;
        }

        .telemetry-item-premium {
          display: flex;
          align-items: center;
          padding: 16px 20px;
          border-bottom: 1px solid var(--border);
          cursor: pointer;
          transition: all 0.2s ease;
          position: relative;
        }

        .telemetry-item-premium:hover {
          background: var(--bg-subtle);
          padding-left: 24px;
        }

        .tel-status-bar {
          position: absolute;
          left: 0;
          top: 0;
          bottom: 0;
          width: 4px;
          transition: width 0.2s ease;
        }

        .telemetry-item-premium:hover .tel-status-bar {
          width: 6px;
        }

        .tel-status-bar.completed { background: var(--success); }
        .tel-status-bar.running { background: var(--blue); }
        .tel-status-bar.failed { background: var(--danger); }

        .tel-icon-side {
          margin-right: 16px;
        }

        .tel-mini-icon {
          width: 24px;
          height: 24px;
          border-radius: 6px;
          display: flex;
          align-items: center;
          justify-content: center;
          color: white;
        }

        .tel-mini-icon.completed { background: var(--success); }
        .tel-mini-icon.running { background: var(--blue); animation: spin 2s linear infinite; }
        .tel-mini-icon.failed { background: var(--danger); }

        .tel-main {
          flex: 1;
          display: flex;
          flex-direction: column;
          gap: 4px;
        }

        .tel-row-top {
          display: flex;
          justify-content: space-between;
          align-items: center;
        }

        .tel-pipeline-name {
          font-size: 0.9rem;
          font-weight: 700;
          color: var(--ink-0);
          letter-spacing: -0.01em;
        }

        .tel-timestamp {
          font-size: 0.75rem;
          font-weight: 600;
          color: var(--ink-3);
          font-family: 'DM Mono', monospace;
        }

        .tel-row-bottom {
          display: flex;
          justify-content: space-between;
          align-items: center;
        }

        .tel-meta-badges {
          display: flex;
          align-items: center;
          gap: 12px;
        }

        .tel-badge-agent {
          display: inline-flex;
          align-items: center;
          font-size: 0.75rem;
          font-weight: 600;
          color: var(--ink-2);
          background: var(--bg-subtle);
          padding: 2px 8px;
          border-radius: 4px;
          border: 1px solid var(--border);
        }

        .tel-msg-preview {
          font-size: 0.75rem;
          color: var(--ink-3);
          max-width: 300px;
          overflow: hidden;
          text-overflow: ellipsis;
          white-space: nowrap;
        }

        .status-tag {
          font-size: 0.65rem;
          font-weight: 800;
          text-transform: uppercase;
          letter-spacing: 0.05em;
          padding: 2px 6px;
          border-radius: 4px;
        }

        .status-tag.completed { color: var(--success); background: var(--success-soft); }
        .status-tag.running { color: var(--blue); background: var(--blue-soft); }
        .status-tag.failed { color: var(--danger); background: var(--danger-soft); }

        .agent-item-row {
          display: flex;
          align-items: center;
          gap: 12px;
          padding: 14px;
          border: 1px solid var(--border);
          border-radius: var(--r-lg);
          margin-bottom: 10px;
          transition: all 0.2s ease;
          text-decoration: none;
          background: var(--surface);
        }

        .agent-item-row:hover {
          background: var(--bg-subtle);
          transform: translateX(4px);
          border-color: var(--blue-border);
        }

        .agent-avatar {
          width: 36px;
          height: 36px;
          border-radius: 10px;
          display: flex;
          align-items: center;
          justify-content: center;
          font-weight: 700;
          font-size: 1rem;
        }

        .agent-info {
          flex: 1;
        }

        .agent-name {
          font-size: 0.95rem;
          font-weight: 700;
          color: var(--ink-0);
          display: block;
        }

        .agent-type {
          font-size: 0.72rem;
          color: var(--ink-3);
          font-weight: 500;
        }

        .system-info-list {
          padding: 4px;
        }

        .info-item {
          padding: 8px 0;
          border-bottom: 1px dashed var(--border);
        }

        .info-item:last-child { border-bottom: none; }

        .run-detail {
          padding: 8px;
        }

        .log-area pre {
          background: #0a0a0a;
          border: 1px solid #222;
          box-shadow: inset 0 2px 10px rgba(0,0,0,0.5);
        }

        @keyframes spin {
          from { transform: rotate(0deg); }
          to { transform: rotate(360deg); }
        }

        @media (max-width: 1100px) {
          .dashboard-main-grid {
            grid-template-columns: 1fr;
          }
        }
      `}</style>
    </div>
  );
}

