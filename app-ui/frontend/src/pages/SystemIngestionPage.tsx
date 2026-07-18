import { useEffect, useRef, useState } from "react";
import { getIngestionRuns, triggerRun } from "../api/client";
import type { IngestionRun } from "../api/types";
import { AppIcon, Spinner } from "../components/index";

const POLL_MS = 3000;

function formatEta(seconds?: number | null): string {
  if (seconds == null || seconds < 0) return "—";
  if (seconds < 60) return `${seconds}s`;
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return `${m}m ${s}s`;
}

function formatDuration(startedUtc?: string, endedUtc?: string): string {
  if (!startedUtc) return "—";
  const start = new Date(startedUtc).getTime();
  const end = endedUtc ? new Date(endedUtc).getTime() : Date.now();
  const secs = Math.max(0, Math.round((end - start) / 1000));
  if (secs < 60) return `${secs}s`;
  const m = Math.floor(secs / 60);
  return `${m}m ${secs % 60}s`;
}

export function SystemIngestionPage() {
  const [runs, setRuns]       = useState<IngestionRun[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError]     = useState("");
  const [triggering, setTriggering] = useState(false);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const load = async (showSpinner = true) => {
    if (showSpinner) setLoading(true);
    setError("");
    try {
      const data = await getIngestionRuns(1, 100);
      setRuns(data.items);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load ingestion history");
    } finally {
      if (showSpinner) setLoading(false);
    }
  };

  useEffect(() => { void load(); }, []);

  // Poll while any run is in progress — mirrors PipelineControlPanel's interval pattern.
  useEffect(() => {
    const hasRunning = runs.some((r) => r.status === "running");
    if (hasRunning && pollRef.current == null) {
      pollRef.current = setInterval(() => void load(false), POLL_MS);
    } else if (!hasRunning && pollRef.current != null) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
    return () => {
      if (pollRef.current != null) {
        clearInterval(pollRef.current);
        pollRef.current = null;
      }
    };
  }, [runs]);

  const runIngestion = async () => {
    setTriggering(true);
    setError("");
    try {
      await triggerRun("rag_ingest");
      await load(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to start ingestion");
    } finally {
      setTriggering(false);
    }
  };

  const anyRunning = runs.some((r) => r.status === "running");

  return (
    <div className="page-stack fade-in">
      <div className="page-header">
        <div>
          <h1>Website Ingestion</h1>
          <p className="page-sub">Crawl gnxtsystems.com and refresh the knowledge base used by the LinkedIn reply generator.</p>
        </div>
        <div className="page-header-actions">
          <button className="btn-secondary" onClick={() => void load()} disabled={loading}>
            {loading ? <Spinner size={13} /> : <AppIcon name="refresh" size={13} />}
            Refresh
          </button>
          <button className="btn-primary" onClick={() => void runIngestion()} disabled={triggering || anyRunning}>
            {triggering || anyRunning ? <Spinner size={13} /> : <AppIcon name="globe" size={13} />}
            {anyRunning ? "Ingestion running…" : "Run Ingestion"}
          </button>
        </div>
      </div>

      {error && <div className="error-banner">{error}</div>}

      <div className="data-grid-wrapper">
        <div className="grid-toolbar">
          <span className="grid-count">{runs.length} run{runs.length === 1 ? "" : "s"}</span>
        </div>

        <div style={{ overflowX: "auto" }}>
          <table className="data-table">
            <thead>
              <tr>
                <th>ID</th>
                <th>Target</th>
                <th>Status</th>
                <th style={{ minWidth: 180 }}>Progress</th>
                <th>ETA</th>
                <th>Pages</th>
                <th>Chunks</th>
                <th>Duration</th>
                <th>Started</th>
              </tr>
            </thead>
            <tbody>
              {loading ? (
                <tr><td colSpan={9} className="loading-row"><Spinner size={16} /> &nbsp;Loading ingestion history…</td></tr>
              ) : runs.length === 0 ? (
                <tr>
                  <td colSpan={9}>
                    <div className="empty-state" style={{ padding: "52px" }}>
                      <AppIcon name="globe" size={32} />
                      <p>No ingestion runs yet. Click "Run Ingestion" to crawl the site for the first time.</p>
                    </div>
                  </td>
                </tr>
              ) : (
                runs.map((run) => (
                  <tr key={run.id}>
                    <td className="cell-mono">#{run.id}</td>
                    <td style={{ fontSize: "0.8rem", color: "var(--ink-2)", maxWidth: 220, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                      {run.target_url ?? "—"}
                    </td>
                    <td><span className={`status-pill ${run.status}`}>{run.status}</span></td>
                    <td>
                      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                        <div style={{ flex: 1, height: 7, borderRadius: "var(--r-full)", background: "var(--border)", overflow: "hidden" }}>
                          <div
                            style={{
                              width: `${Math.max(0, Math.min(100, run.progress_pct))}%`,
                              height: "100%",
                              background: run.status === "failed" ? "var(--status-failed-fg)" : "var(--status-running-fg)",
                              transition: "width 0.4s ease",
                            }}
                          />
                        </div>
                        <span className="cell-mono" style={{ fontSize: "0.72rem", minWidth: 36, textAlign: "right" }}>
                          {Math.round(run.progress_pct)}%
                        </span>
                      </div>
                      {run.stage && (
                        <span style={{ fontSize: "0.68rem", color: "var(--text-disabled)", textTransform: "uppercase", letterSpacing: "0.04em" }}>
                          {run.stage}
                        </span>
                      )}
                    </td>
                    <td className="cell-mono" style={{ fontSize: "0.76rem" }}>
                      {run.status === "running" ? formatEta(run.eta_seconds) : "—"}
                    </td>
                    <td className="cell-mono" style={{ fontSize: "0.76rem" }}>{run.pages_crawled}</td>
                    <td className="cell-mono" style={{ fontSize: "0.76rem" }}>
                      {run.chunks_stored ?? run.chunks_embedded}/{run.chunks_created}
                    </td>
                    <td className="cell-mono" style={{ fontSize: "0.76rem" }}>
                      {formatDuration(run.started_at_utc, run.ended_at_utc)}
                    </td>
                    <td className="cell-mono" style={{ fontSize: "0.76rem" }}>
                      {run.started_at_utc
                        ? new Date(run.started_at_utc).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })
                        : "—"}
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
