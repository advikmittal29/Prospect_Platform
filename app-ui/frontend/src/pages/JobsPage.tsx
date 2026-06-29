import { FormEvent, useEffect, useMemo, useState } from "react";
import { useOutletContext } from "react-router-dom";
import { getJobs } from "../api/client";
import type { JobRow } from "../api/types";
import type { AgentScopeContextValue } from "../agentScope";
import { Modal } from "../components/Modal";
import { Pagination } from "../components/Pagination";
import { SectionCard } from "../components/SectionCard";

const safe = (v: unknown) => (v == null || v === "" ? "Not available" : String(v));

export function JobsPage() {
  const { activeAgentId } = useOutletContext<AgentScopeContextValue>();
  const [rows, setRows] = useState<JobRow[]>([]);
  const [search, setSearch] = useState("");
  const [keyword, setKeyword] = useState("");
  const [company, setCompany] = useState("");
  const [researched, setResearched] = useState<"all" | "true" | "false">("all");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [page, setPage] = useState(1);
  const [selectedJob, setSelectedJob] = useState<JobRow | null>(null);
  const PAGE_SIZE = 50;
  const [total, setTotal] = useState(0);


  const load = async () => {
    setLoading(true);
    setError("");
    try {
      const r = await getJobs({
        search: search || undefined,
        keyword: keyword || undefined,
        company: company || undefined,
        researched: researched === "all" ? undefined : researched === "true",
        page: page,
        page_size: PAGE_SIZE,
        agent_id: activeAgentId,
      });
      setRows(r.items);
      setTotal(r.total);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load jobs");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void load();
  }, [page, search, keyword, company, researched, activeAgentId]);

  const onFilter = (e: FormEvent) => {
    e.preventDefault();
    setPage(1);
  };

  const pagedRows = useMemo(() => rows.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE), [rows, page]);

  return (
    <div className="page-stack">
      <div className="page-header fade-in">
        <div className="page-header-left">
          <h1>Job Posts</h1>
          <p className="page-sub">
            Ingested job records from Naukri with enrichment status.
            {activeAgentId ? ` (Agent #${activeAgentId})` : ""}
          </p>
        </div>
      </div>

      <SectionCard title="Search and Filter" subtitle="Filter by title, company, keyword, or research status">
        <form className="filter-grid" onSubmit={onFilter}>
          <label>
            Search
            <input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="title / company / location" />
          </label>
          <label>
            Keyword
            <input value={keyword} onChange={(e) => setKeyword(e.target.value)} placeholder="e.g. AI Engineer" />
          </label>
          <label>
            Company
            <input value={company} onChange={(e) => setCompany(e.target.value)} placeholder="e.g. Siemens" />
          </label>
          <label>
            Research Status
            <select value={researched} onChange={(e) => setResearched(e.target.value as "all" | "true" | "false")}>
              <option value="all">All statuses</option>
              <option value="true">Researched</option>
              <option value="false">Pending</option>
            </select>
          </label>
          <div className="form-actions">
            <button className="btn-primary" type="submit" disabled={loading}>
              {loading ? (
                <>
                  <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" className="spin">
                    <path d="M21 12a9 9 0 1 1-6.219-8.56" />
                  </svg>
                  Loading
                </>
              ) : (
                "Apply Filters"
              )}
            </button>
          </div>
        </form>
      </SectionCard>

      {error ? (
        <div className="error-banner">
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" style={{ flexShrink: 0 }}>
            <circle cx="12" cy="12" r="10" />
            <path d="M12 8v4M12 16h.01" />
          </svg>
          {error}
        </div>
      ) : null}

      <SectionCard title="Results" subtitle={`${total} job records found`} noPad>
        <div className="table-wrap fixed-height" style={{ border: "none", borderRadius: 0 }}>
          <table>
            <thead>
              <tr><th>ID</th><th>Job Title</th><th>Company</th><th>Location</th><th>Experience</th><th>Role</th><th>Status</th><th></th></tr>
            </thead>
            <tbody>
              {pagedRows.map((item) => (
                <tr key={item.id}>
                  <td className="cell-mono">#{item.id}</td>
                  <td className="cell-primary">{item.title ?? "-"}</td>
                  <td>{item.company_name ?? "-"}</td>
                  <td>{item.location_text ?? "-"}</td>
                  <td>{item.experience_text ?? "-"}</td>
                  <td>{item.role ?? item.role_category ?? "-"}</td>
                  <td>
                    <span className={`status-pill ${item.researched ? "completed" : "pending"}`}>
                      {item.researched ? "researched" : "pending"}
                    </span>
                  </td>
                  <td>
                    <button className="btn-ghost btn-sm" onClick={() => setSelectedJob(item)}>View</button>
                  </td>
                </tr>
              ))}
              {pagedRows.length === 0 && (
                <tr>
                  <td colSpan={8} style={{ textAlign: "center", color: "var(--ink-2)", padding: "40px", fontStyle: "italic" }}>
                    No jobs found. Try adjusting filters.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
        <Pagination page={page} pageSize={PAGE_SIZE} totalItems={total} onPageChange={setPage} />
      </SectionCard>

      <Modal open={!!selectedJob} onClose={() => setSelectedJob(null)} title={selectedJob?.title ?? "Job Detail"} subtitle={selectedJob?.company_name} size="lg">
        {selectedJob && (
          <>
            <div className="info-grid">
              <article className="info-card"><h4>Company</h4><p>{safe(selectedJob.company_name)}</p></article>
              <article className="info-card"><h4>Location</h4><p>{safe(selectedJob.location_text)}</p></article>
              <article className="info-card"><h4>Experience</h4><p>{safe(selectedJob.experience_text)}</p></article>
              <article className="info-card"><h4>Salary</h4><p>{safe(selectedJob.salary_text)}</p></article>
              <article className="info-card"><h4>Employment Type</h4><p>{safe(selectedJob.employment_type)}</p></article>
              <article className="info-card"><h4>Confidence</h4><p>{selectedJob.extraction_confidence != null ? `${Math.round(selectedJob.extraction_confidence)}%` : "—"}</p></article>
            </div>
            <div className="badge-row">
              <span className="badge">Industry: {safe(selectedJob.industry)}</span>
              <span className="badge">Dept: {safe(selectedJob.department)}</span>
              <span className="badge">Education: {safe(selectedJob.education)}</span>
              <span className="badge">Posted: {safe(selectedJob.posted_date || selectedJob.posted_relative)}</span>
            </div>
            <div className="button-row">
              {selectedJob.job_url && <a className="btn-primary" href={selectedJob.job_url} target="_blank" rel="noreferrer">Open Job Listing</a>}
              {selectedJob.canonical_job_url && <a className="btn-ghost" href={selectedJob.canonical_job_url} target="_blank" rel="noreferrer">Canonical URL</a>}
            </div>
          </>
        )}
      </Modal>
    </div>
  );
}
