import { useEffect, useMemo, useState } from "react";
import { getAgents, getRunDetail, getRuns } from "../api/client";
import type { AgentDefinitionRow, PipelineRun } from "../api/types";
import { AppIcon, Modal, Pagination, Spinner } from "../components/index";

const PAGE_SIZE = 20;

export function SystemRunsPage() {
  const [runs, setRuns]     = useState<PipelineRun[]>([]);
  const [agents, setAgents] = useState<AgentDefinitionRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError]     = useState("");
  const [page, setPage]       = useState(1);
  const [filterStatus, setFilterStatus] = useState("");
  const [filterPipeline, setFilterPipeline] = useState("");
  const [selectedRun, setSelectedRun] = useState<PipelineRun | null>(null);

  const load = async () => {
    setLoading(true);
    setError("");
    try {
      const [runsData, agentsData] = await Promise.all([getRuns(1, 200), getAgents()]);
      setRuns(runsData.items);
      setAgents(agentsData.items);
      setPage(1);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load run history");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { void load(); }, []);

  const openDetail = async (run: PipelineRun) => {
    try { setSelectedRun(await getRunDetail(run.id)); }
    catch { setSelectedRun(run); }
  };

  const filtered = useMemo(() => {
    return runs.filter((r) => {
      if (filterStatus && r.status !== filterStatus) return false;
      if (filterPipeline && r.pipeline !== filterPipeline) return false;
      return true;
    });
  }, [runs, filterStatus, filterPipeline]);

  const paged = useMemo(() => filtered.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE), [filtered, page]);

  const agentName = (id?: number | null) => agents.find((a) => a.id === id)?.name ?? "System";

  return (
    <div className="page-stack fade-in">
      <div className="page-header">
        <div>
          <h1>Run History</h1>
          <p className="page-sub">Complete log of all pipeline executions across every agent.</p>
        </div>
        <div className="page-header-actions">
          <button className="btn-secondary" onClick={load} disabled={loading}>
            {loading ? <Spinner size={13} /> : <AppIcon name="refresh" size={13} />}
            Refresh
          </button>
        </div>
      </div>

      {error && <div className="error-banner">{error}</div>}

      <div className="data-grid-wrapper">
        <div className="grid-toolbar">
          <select
            className="grid-filter-select"
            value={filterPipeline}
            onChange={(e) => { setFilterPipeline(e.target.value); setPage(1); }}
          >
            <option value="">All Pipelines</option>
            <option value="ingest">Ingest</option>
            <option value="research">Research</option>
            <option value="intelligence">Intelligence</option>
            <option value="candidate_hunt">Candidate Hunt</option>
          </select>
          <select
            className="grid-filter-select"
            value={filterStatus}
            onChange={(e) => { setFilterStatus(e.target.value); setPage(1); }}
          >
            <option value="">All Statuses</option>
            <option value="completed">Completed</option>
            <option value="running">Running</option>
            <option value="failed">Failed</option>
            <option value="pending">Pending</option>
          </select>
          <span className="grid-count">{filtered.length} runs</span>
        </div>

        <div style={{ overflowX: "auto" }}>
          <table className="data-table">
            <thead>
              <tr>
                <th>ID</th>
                <th>Pipeline</th>
                <th>Agent</th>
                <th>Status</th>
                <th>Started</th>
                <th>Ended</th>
                <th>Message</th>
                <th style={{ width: 60 }}></th>
              </tr>
            </thead>
            <tbody>
              {loading ? (
                <tr><td colSpan={8} className="loading-row"><Spinner size={16} /> &nbsp;Loading run history…</td></tr>
              ) : paged.length === 0 ? (
                <tr>
                  <td colSpan={8}>
                    <div className="empty-state" style={{ padding: "52px" }}>
                      <AppIcon name="pulse" size={32} />
                      <p>No pipeline runs match the current filters.</p>
                    </div>
                  </td>
                </tr>
              ) : (
                paged.map((run) => (
                  <tr key={run.id}>
                    <td className="cell-mono">#{run.id}</td>
                    <td className="cell-primary" style={{ textTransform: "uppercase", letterSpacing: "0.04em", fontSize: "0.78rem" }}>
                      {run.pipeline}
                    </td>
                    <td style={{ fontSize: "0.82rem", color: "var(--ink-2)" }}>{agentName(run.agent_id)}</td>
                    <td><span className={`status-pill ${run.status}`}>{run.status}</span></td>
                    <td className="cell-mono" style={{ fontSize: "0.76rem" }}>
                      {run.started_at_utc
                        ? new Date(run.started_at_utc).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })
                        : "—"}
                    </td>
                    <td className="cell-mono" style={{ fontSize: "0.76rem" }}>
                      {run.ended_at_utc
                        ? new Date(run.ended_at_utc).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })
                        : "—"}
                    </td>
                    <td style={{ maxWidth: 260 }}>
                      <span style={{ fontSize: "0.78rem", color: "var(--ink-2)", display: "block", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                        {run.message ?? "—"}
                      </span>
                    </td>
                    <td>
                      <button className="btn-icon" onClick={() => void openDetail(run)} title="View logs">
                        <AppIcon name="eye" size={13} />
                      </button>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>

        <div className="grid-footer">
          <Pagination page={page} pageSize={PAGE_SIZE} totalItems={filtered.length} onPageChange={setPage} />
        </div>
      </div>

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
                <span className="kv-label">Agent</span>
                <span className="kv-value">{agentName(selectedRun.agent_id)}</span>
              </div>
              <div className="kv-item">
                <span className="kv-label">Started</span>
                <span className="kv-value">{selectedRun.started_at_utc ?? "—"}</span>
              </div>
              <div className="kv-item">
                <span className="kv-label">Ended</span>
                <span className="kv-value">{selectedRun.ended_at_utc ?? "Still running…"}</span>
              </div>
            </div>
            <div>
              <div className="form-label" style={{ marginBottom: 8 }}>Execution Log</div>
              <pre style={{
                background: "var(--ink-0)", color: "#10b981",
                padding: "16px", borderRadius: "var(--r-md)",
                fontFamily: "var(--font-mono)", fontSize: "0.75rem",
                maxHeight: 420, overflow: "auto", lineHeight: 1.65,
              }}>
                {selectedRun.log_text || selectedRun.message || "No log output available for this run."}
              </pre>
            </div>
          </div>
        )}
      </Modal>
    </div>
  );
}
