import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { getAgents, getDashboardSummary, getRunDetail } from "../api/client";
import type { AgentDefinitionRow, DashboardSummary, PipelineRun } from "../api/types";
import { AppIcon, Modal, SectionCard, Spinner } from "../components/index";

export function OverviewPage() {
  const [agents, setAgents] = useState<AgentDefinitionRow[]>([]);
  const [summary, setSummary] = useState<DashboardSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [selectedRun, setSelectedRun] = useState<PipelineRun | null>(null);

  const load = async () => {
    setError("");
    setLoading(true);
    try {
      const [dashSummary, agentsResp] = await Promise.all([
        getDashboardSummary(null),
        getAgents(),
      ]);
      setSummary(dashSummary);
      setAgents(agentsResp.items ?? []);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load overview");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { void load(); }, []);

  const openRun = async (run: PipelineRun) => {
    try {
      const detail = await getRunDetail(run.id);
      setSelectedRun(detail);
    } catch {
      setSelectedRun(run);
    }
  };

  const metrics = summary?.metrics ?? {};
  const recentRuns = summary?.recent_runs ?? [];
  const activeAgents = agents.filter((a) => a.status === "active");
  const successRate = recentRuns.length
    ? Math.round((recentRuns.filter((r) => r.status === "completed").length / recentRuns.length) * 100)
    : 100;

  const kpis = [
    { label: "Active Agents", value: activeAgents.length, icon: "agents", color: "blue", trend: `${agents.length} total registered` },
    { label: "Companies", value: metrics.companies_total ?? 0, icon: "companies", color: "violet", trend: `${metrics.companies_completed ?? 0} fully researched` },
    { label: "Prospects", value: metrics.prospects_total ?? 0, icon: "prospects", color: "teal", trend: `${metrics.prospects_hot ?? 0} high-intent` },
    { label: "Pipeline Health", value: `${successRate}%`, icon: "check", color: "emerald", trend: "Execution success rate" },
  ];

  return (
    <div className="page-stack fade-in">
      <div className="page-header">
        <div>
          <h1>Platform Overview</h1>
          <p className="page-sub">System-wide performance and agent fleet status.</p>
        </div>
        <div className="page-header-actions">
          <button className="btn-secondary" onClick={() => void load()} disabled={loading}>
            {loading ? <Spinner size={13} /> : <AppIcon name="refresh" size={13} />}
            {loading ? "Loading…" : "Refresh"}
          </button>
        </div>
      </div>

      {error && <div className="error-banner">{error}</div>}

      {/* KPI Row */}
      <div className="kpi-grid">
        {kpis.map((kpi) => (
          <div key={kpi.label} className="kpi-card">
            <div className={`kpi-icon ${kpi.color}`}>
              <AppIcon name={kpi.icon as any} size={20} />
            </div>
            <div className="kpi-body">
              <span className="kpi-label">{kpi.label}</span>
              <span className="kpi-value">{kpi.value}</span>
              <span className="kpi-trend">{kpi.trend}</span>
            </div>
          </div>
        ))}
      </div>

      {/* Main grid */}
      <div style={{ display: "grid", gridTemplateColumns: "1fr 320px", gap: 24 }}>
        {/* Execution stream */}
        <SectionCard
          title="System Execution Stream"
          subtitle="All pipeline runs across all agents"
          noPad
          headerAction={
            <Link to="/system/runs" style={{ fontSize: "0.78rem", color: "var(--blue)", fontWeight: 600 }}>
              View all →
            </Link>
          }
        >
          <div className="activity-list">
            {loading ? (
              <div className="empty-state" style={{ padding: "40px" }}>
                <Spinner size={24} />
                <p>Loading execution data…</p>
              </div>
            ) : recentRuns.length === 0 ? (
              <div className="empty-state" style={{ padding: "48px" }}>
                <AppIcon name="pulse" size={32} />
                <p>No pipeline activity yet. Trigger a run from an agent dashboard.</p>
              </div>
            ) : (
              recentRuns.slice(0, 12).map((run) => {
                const agent = agents.find((a) => a.id === run.agent_id);
                return (
                  <div key={run.id} className="activity-item" onClick={() => void openRun(run)}>
                    <div className={`activity-bar ${run.status}`} />
                    <div className="activity-body">
                      <div className="activity-top">
                        <span className="activity-name">{run.pipeline}</span>
                        <span className="activity-time">
                          {run.started_at_utc
                            ? new Date(run.started_at_utc).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })
                            : "—"}
                        </span>
                      </div>
                      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                        <span className="activity-msg">
                          {agent ? agent.name : "System"} · {run.message || `Pipeline ${run.status}`}
                        </span>
                        <span className={`status-pill ${run.status}`}>{run.status}</span>
                      </div>
                    </div>
                  </div>
                );
              })
            )}
          </div>
        </SectionCard>

        {/* Agent fleet */}
        <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
          <SectionCard title="Agent Fleet" subtitle="Registered autonomous agents" noPad>
            {agents.length === 0 ? (
              <div className="empty-state" style={{ padding: "32px" }}>
                <AppIcon name="agents" size={28} />
                <p>No agents registered in the system.</p>
              </div>
            ) : (
              <div>
                {agents.map((agent) => (
                  <Link
                    key={agent.id}
                    to={`/agent/${agent.id}/dashboard`}
                    style={{
                      display: "flex", alignItems: "center", gap: 12,
                      padding: "12px 16px",
                      borderBottom: "1px solid var(--border)",
                      transition: "background 150ms",
                      textDecoration: "none",
                    }}
                    onMouseEnter={(e) => (e.currentTarget.style.background = "var(--bg-subtle)")}
                    onMouseLeave={(e) => (e.currentTarget.style.background = "")}
                  >
                    <div
                      style={{
                        width: 34, height: 34, borderRadius: 9,
                        background: agent.status === "active" ? "var(--blue)" : "var(--bg-subtle)",
                        color: agent.status === "active" ? "white" : "var(--ink-2)",
                        display: "flex", alignItems: "center", justifyContent: "center",
                        fontWeight: 700, fontSize: "0.88rem", flexShrink: 0,
                      }}
                    >
                      {agent.name[0]?.toUpperCase()}
                    </div>
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div style={{ fontSize: "0.85rem", fontWeight: 650, color: "var(--ink-0)", marginBottom: 1 }}>
                        {agent.name}
                      </div>
                      <div style={{ fontSize: "0.72rem", color: "var(--ink-3)", textTransform: "capitalize" }}>
                        {agent.agent_type ?? "sales"} agent
                      </div>
                    </div>
                    <span className={`status-pill ${agent.status}`}>{agent.status}</span>
                  </Link>
                ))}
              </div>
            )}
          </SectionCard>

          <SectionCard title="System Info">
            <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
              {[
                ["Platform", "ProspectOS v2.5"],
                ["Orchestration", "Deterministic"],
                ["Agents Loaded", String(agents.length)],
                ["System Status", "Operational"],
              ].map(([label, value]) => (
                <div key={label} style={{ display: "flex", justifyContent: "space-between", fontSize: "0.83rem" }}>
                  <span style={{ color: "var(--ink-2)" }}>{label}</span>
                  <span style={{ fontWeight: 650, color: "var(--ink-0)" }}>{value}</span>
                </div>
              ))}
            </div>
          </SectionCard>
        </div>
      </div>

      {/* Run detail modal */}
      <Modal
        open={!!selectedRun}
        onClose={() => setSelectedRun(null)}
        title={`Run #${selectedRun?.id} — ${selectedRun?.pipeline ?? ""}`}
        size="lg"
      >
        {selectedRun && (
          <div style={{ display: "flex", flexDirection: "column", gap: 20 }}>
            <div className="kv-grid">
              <div className="kv-item">
                <span className="kv-label">Status</span>
                <span className={`status-pill ${selectedRun.status}`}>{selectedRun.status}</span>
              </div>
              <div className="kv-item">
                <span className="kv-label">Pipeline</span>
                <span className="kv-value">{selectedRun.pipeline}</span>
              </div>
              <div className="kv-item">
                <span className="kv-label">Started</span>
                <span className="kv-value">{selectedRun.started_at_utc ?? "—"}</span>
              </div>
              <div className="kv-item">
                <span className="kv-label">Ended</span>
                <span className="kv-value">{selectedRun.ended_at_utc ?? "Still running"}</span>
              </div>
            </div>
            <div>
              <div className="form-label" style={{ marginBottom: 8 }}>Execution Log</div>
              <pre style={{
                background: "var(--ink-0)", color: "#10b981",
                padding: "16px", borderRadius: "var(--r-md)",
                fontFamily: "var(--font-mono)", fontSize: "0.75rem",
                maxHeight: 360, overflow: "auto", lineHeight: 1.6,
              }}>
                {selectedRun.log_text || selectedRun.message || "No log output available."}
              </pre>
            </div>
          </div>
        )}
      </Modal>
    </div>
  );
}
