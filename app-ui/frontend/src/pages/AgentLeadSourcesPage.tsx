import { useEffect, useMemo, useState } from "react";
import { useParams } from "react-router-dom";
import { getJobs } from "../api/client";
import type { JobRow } from "../api/types";
import { AppIcon, Modal, Pagination, Spinner } from "../components/index";

const PAGE_SIZE = 15;
const safe = (v: unknown) => (v == null || v === "" ? "—" : String(v));

type SortKey = "title" | "company_name" | "posted_date" | "employment_type";
type SortDir = "asc" | "desc";

export function AgentLeadSourcesPage() {
  const { agentId } = useParams<{ agentId: string }>();
  const id = Number(agentId);

  const [rows, setRows]           = useState<JobRow[]>([]);
  const [search, setSearch]       = useState("");
  const [researched, setResearched] = useState<"" | "true" | "false">("");
  const [loading, setLoading]     = useState(false);
  const [error, setError]         = useState("");
  const [page, setPage]           = useState(1);
  const [sortKey, setSortKey]     = useState<SortKey>("posted_date");
  const [sortDir, setSortDir]     = useState<SortDir>("desc");
  const [selectedJob, setSelectedJob] = useState<JobRow | null>(null);

  const load = async () => {
    setLoading(true);
    setError("");
    try {
      const r = await getJobs({
        search: search || undefined,
        researched: researched === "" ? undefined : researched === "true",
        page_size: 200,
        agent_id: id,
      });
      setRows(r.items);
      setPage(1);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load lead sources");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { void load(); }, [agentId]);

  const toggleSort = (key: SortKey) => {
    if (sortKey === key) setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    else { setSortKey(key); setSortDir("asc"); }
  };

  const sorted = useMemo(() => {
    return [...rows].sort((a, b) => {
      let av = a[sortKey] ?? "";
      let bv = b[sortKey] ?? "";
      const avStr = typeof av === "string" ? av.toLowerCase() : av;
      const bvStr = typeof bv === "string" ? bv.toLowerCase() : bv;
      if (avStr < bvStr) return sortDir === "asc" ? -1 : 1;
      if (avStr > bvStr) return sortDir === "asc" ? 1 : -1;
      return 0;
    });
  }, [rows, sortKey, sortDir]);

  const paged = useMemo(() => sorted.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE), [sorted, page]);

  const SortIcon = ({ col }: { col: SortKey }) => (
    <span style={{ marginLeft: 4, opacity: sortKey === col ? 1 : 0.3 }}>
      {sortKey === col && sortDir === "desc" ? "↓" : "↑"}
    </span>
  );

  return (
    <div className="page-stack fade-in">
      <div className="page-header">
        <div>
          <h1>Lead Sources</h1>
          <p className="page-sub">Ingested job signals used to identify target companies.</p>
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
          <div className="grid-search">
            <AppIcon name="leads" size={14} className="grid-search-icon" />
            <input
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && void load()}
              placeholder="Filter by job title, company, keyword…"
            />
          </div>
          <select
            className="grid-filter-select"
            value={researched}
            onChange={(e) => {
              setResearched(e.target.value as any);
              setTimeout(() => void load(), 0);
            }}
          >
            <option value="">All Sources</option>
            <option value="true">Researched</option>
            <option value="false">Pending Research</option>
          </select>
          <button className="btn-primary" onClick={load} disabled={loading} style={{ flexShrink: 0 }}>
            Search
          </button>
          <span className="grid-count">{rows.length} sources</span>
        </div>

        <div style={{ overflowX: "auto" }}>
          <table className="data-table">
            <thead>
              <tr>
                <th className={`sortable ${sortKey === "title" ? "sort-active" : ""}`} onClick={() => toggleSort("title")}>
                  Job Title <SortIcon col="title" />
                </th>
                <th className={`sortable ${sortKey === "company_name" ? "sort-active" : ""}`} onClick={() => toggleSort("company_name")}>
                  Company <SortIcon col="company_name" />
                </th>
                <th>Keyword</th>
                <th>Location</th>
                <th className={`sortable ${sortKey === "employment_type" ? "sort-active" : ""}`} onClick={() => toggleSort("employment_type")}>
                  Type <SortIcon col="employment_type" />
                </th>
                <th className={`sortable ${sortKey === "posted_date" ? "sort-active" : ""}`} onClick={() => toggleSort("posted_date")}>
                  Posted <SortIcon col="posted_date" />
                </th>
                <th>Researched</th>
                <th style={{ width: 60 }}></th>
              </tr>
            </thead>
            <tbody>
              {loading ? (
                <tr><td colSpan={8} className="loading-row"><Spinner size={16} /> &nbsp;Loading lead sources…</td></tr>
              ) : paged.length === 0 ? (
                <tr>
                  <td colSpan={8}>
                    <div className="empty-state" style={{ padding: "52px" }}>
                      <AppIcon name="leads" size={32} />
                      <p>No lead sources ingested yet. Run the Ingest pipeline to pull job listings.</p>
                    </div>
                  </td>
                </tr>
              ) : (
                paged.map((row) => (
                  <tr key={row.id}>
                    <td className="cell-primary" style={{ maxWidth: 240 }}>
                      <span style={{ display: "block", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                        {row.title ?? "—"}
                      </span>
                    </td>
                    <td className="cell-accent">{safe(row.company_name)}</td>
                    <td className="cell-mono" style={{ fontSize: "0.76rem" }}>{safe(row.search_keyword)}</td>
                    <td style={{ fontSize: "0.8rem" }}>{safe(row.location_text ?? row.search_location)}</td>
                    <td>
                      <span style={{ fontSize: "0.75rem", padding: "2px 6px", borderRadius: "var(--r-sm)", background: "var(--bg-subtle)", color: "var(--ink-2)", fontWeight: 600 }}>
                        {safe(row.employment_type)}
                      </span>
                    </td>
                    <td className="cell-mono" style={{ fontSize: "0.76rem" }}>{safe(row.posted_relative ?? row.posted_date)}</td>
                    <td>
                      <span className={`status-pill ${row.researched ? "completed" : "pending"}`}>
                        {row.researched ? "yes" : "no"}
                      </span>
                    </td>
                    <td>
                      <div className="row-actions">
                        <button className="btn-icon" onClick={() => setSelectedJob(row)} title="View details">
                          <AppIcon name="eye" size={13} />
                        </button>
                        {row.job_url && (
                          <a
                            href={row.job_url}
                            target="_blank"
                            rel="noreferrer"
                            className="btn-icon"
                            title="Open job listing"
                          >
                            <AppIcon name="externalLink" size={13} />
                          </a>
                        )}
                      </div>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>

        <div className="grid-footer">
          <Pagination page={page} pageSize={PAGE_SIZE} totalItems={sorted.length} onPageChange={setPage} />
        </div>
      </div>

      {/* Job Detail Modal */}
      <Modal
        open={!!selectedJob}
        onClose={() => setSelectedJob(null)}
        title={selectedJob?.title ?? "Job Detail"}
        size="lg"
      >
        {selectedJob && (
          <div style={{ display: "flex", flexDirection: "column", gap: 20 }}>
            <div className="kv-grid">
              <div className="kv-item">
                <span className="kv-label">Company</span>
                <span className="kv-value">{safe(selectedJob.company_name)}</span>
              </div>
              <div className="kv-item">
                <span className="kv-label">Location</span>
                <span className="kv-value">{safe(selectedJob.location_text ?? selectedJob.search_location)}</span>
              </div>
              <div className="kv-item">
                <span className="kv-label">Employment Type</span>
                <span className="kv-value">{safe(selectedJob.employment_type)}</span>
              </div>
              <div className="kv-item">
                <span className="kv-label">Industry</span>
                <span className="kv-value">{safe(selectedJob.industry)}</span>
              </div>
              <div className="kv-item">
                <span className="kv-label">Department</span>
                <span className="kv-value">{safe(selectedJob.department)}</span>
              </div>
              <div className="kv-item">
                <span className="kv-label">Role Category</span>
                <span className="kv-value">{safe(selectedJob.role_category)}</span>
              </div>
              <div className="kv-item">
                <span className="kv-label">Experience</span>
                <span className="kv-value">{safe(selectedJob.experience_text)}</span>
              </div>
              <div className="kv-item">
                <span className="kv-label">Salary</span>
                <span className="kv-value">{safe(selectedJob.salary_text)}</span>
              </div>
              <div className="kv-item">
                <span className="kv-label">Posted</span>
                <span className="kv-value">{safe(selectedJob.posted_relative ?? selectedJob.posted_date)}</span>
              </div>
              <div className="kv-item">
                <span className="kv-label">Source Keyword</span>
                <span className="kv-value cell-mono" style={{ fontFamily: "var(--font-mono)", fontSize: "0.8rem" }}>{safe(selectedJob.search_keyword)}</span>
              </div>
              <div className="kv-item">
                <span className="kv-label">Researched</span>
                <span className={`status-pill ${selectedJob.researched ? "completed" : "pending"}`}>
                  {selectedJob.researched ? "Yes" : "No"}
                </span>
              </div>
              <div className="kv-item">
                <span className="kv-label">Confidence</span>
                <span className="kv-value">{selectedJob.extraction_confidence != null ? `${Math.round(selectedJob.extraction_confidence)}%` : "—"}</span>
              </div>
            </div>
            {(selectedJob.job_url ?? selectedJob.canonical_job_url) && (
              <a
                href={selectedJob.canonical_job_url ?? selectedJob.job_url}
                target="_blank"
                rel="noreferrer"
                className="btn-secondary"
                style={{ width: "fit-content" }}
              >
                <AppIcon name="externalLink" size={13} /> Open Job Listing
              </a>
            )}
          </div>
        )}
      </Modal>
    </div>
  );
}
