import { FormEvent, useEffect, useMemo, useState } from "react";
import { useOutletContext } from "react-router-dom";
import { getCompanies, getCompanyDetail } from "../api/client";
import type { CompanyRow, ProspectRow } from "../api/types";
import type { AgentScopeContextValue } from "../agentScope";
import { Modal } from "../components/Modal";
import { Pagination } from "../components/Pagination";
import { SectionCard } from "../components/SectionCard";

const PAGE_SIZE = 10;
const safe = (v: unknown) => (v == null || v === "" ? "Not available" : String(v));

export function CompaniesPage() {
  const { activeAgentId, agentScopeVersion } = useOutletContext<AgentScopeContextValue>();
  const [rows, setRows] = useState<CompanyRow[]>([]);
  const [status, setStatus] = useState("");
  const [search, setSearch] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [page, setPage] = useState(1);
  const [selectedCompany, setSelectedCompany] = useState<CompanyRow | null>(null);
  const [companyProspects, setCompanyProspects] = useState<ProspectRow[]>([]);

  const load = async () => {
    setLoading(true);
    setError("");
    try {
      const r = await getCompanies({
        status: status || undefined,
        search: search || undefined,
        page_size: 200,
        agent_id: activeAgentId,
      });
      setRows(r.items);
      setPage(1);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load companies");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [agentScopeVersion]);

  const onFilter = (e: FormEvent) => {
    e.preventDefault();
    void load();
  };

  const openCompanyModal = async (id: number) => {
    try {
      const d = await getCompanyDetail(id, activeAgentId);
      setSelectedCompany(d.company);
      setCompanyProspects(d.prospects);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load company");
    }
  };

  const pagedRows = useMemo(() => rows.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE), [rows, page]);

  return (
    <div className="page-stack">
      <div className="page-header fade-in">
        <div className="page-header-left">
          <h1>Company Intelligence</h1>
          <p className="page-sub">
            Researched companies with LinkedIn profiles and prospect shortlists.
            {activeAgentId ? ` (Agent #${activeAgentId})` : ""}
          </p>
        </div>
      </div>

      <SectionCard title="Search and Filter" subtitle="Filter by research state or company name">
        <form className="filter-grid" onSubmit={onFilter}>
          <label>
            Research Status
            <select value={status} onChange={(e) => setStatus(e.target.value)}>
              <option value="">All statuses</option>
              <option value="pending">Pending</option>
              <option value="in_progress">In Progress</option>
              <option value="completed">Completed</option>
              <option value="failed">Failed</option>
              <option value="skipped">Skipped</option>
            </select>
          </label>
          <label>
            Search
            <input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="company / industry / location" />
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

      <SectionCard title="Company Results" subtitle={`${rows.length} companies found`} noPad>
        <div className="table-wrap fixed-height" style={{ border: "none", borderRadius: 0 }}>
          <table>
            <thead>
              <tr><th>ID</th><th>Company</th><th>Status</th><th>Industry</th><th>Location</th><th>Employees</th><th>Prospects</th><th></th></tr>
            </thead>
            <tbody>
              {pagedRows.map((row) => (
                <tr key={row.id}>
                  <td className="cell-mono">#{row.id}</td>
                  <td className="cell-primary">{row.company_name}</td>
                  <td><span className={`status-pill ${row.research_status ?? "pending"}`}>{row.research_status ?? "pending"}</span></td>
                  <td>{row.industry ?? "-"}</td>
                  <td>{row.location ?? "-"}</td>
                  <td>{row.employee_range ?? "-"}</td>
                  <td className="cell-mono">{row.prospect_count ?? 0}</td>
                  <td>
                    <button className="btn-ghost btn-sm" onClick={() => void openCompanyModal(row.id)}>View</button>
                  </td>
                </tr>
              ))}
              {pagedRows.length === 0 && (
                <tr>
                  <td colSpan={8} style={{ textAlign: "center", color: "var(--ink-2)", padding: "40px", fontStyle: "italic" }}>
                    No companies found
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
        <Pagination page={page} pageSize={PAGE_SIZE} totalItems={rows.length} onPageChange={setPage} />
      </SectionCard>

      <Modal open={!!selectedCompany} onClose={() => setSelectedCompany(null)} title={selectedCompany?.company_name ?? "Company Detail"} subtitle="Company profile and prospect shortlist" size="xl">
        {selectedCompany && (
          <>
            <div className="info-grid">
              <article className="info-card"><h4>Tagline</h4><p>{safe(selectedCompany.tagline)}</p></article>
              <article className="info-card"><h4>Industry</h4><p>{safe(selectedCompany.industry)}</p></article>
              <article className="info-card"><h4>Location</h4><p>{safe(selectedCompany.location)}</p></article>
              <article className="info-card"><h4>Employees</h4><p>{safe(selectedCompany.employee_range)}</p></article>
              <article className="info-card"><h4>Followers</h4><p>{safe(selectedCompany.followers)}</p></article>
              <article className="info-card"><h4>Match Confidence</h4><p>{safe(selectedCompany.linkedin_match_confidence)}</p></article>
            </div>
            {selectedCompany.linkedin_url && (
              <div className="button-row">
                <a className="btn-primary" href={selectedCompany.linkedin_url} target="_blank" rel="noreferrer">View LinkedIn Page</a>
              </div>
            )}
            <section>
              <h3>Prospect Shortlist</h3>
              <div className="table-wrap slim-height">
                <table>
                  <thead><tr><th>ID</th><th>Name</th><th>Role Bucket</th><th>Relevance Score</th><th>Bucket</th><th>Dossier</th></tr></thead>
                  <tbody>
                    {companyProspects.map((p) => (
                      <tr key={p.id}>
                        <td className="cell-mono">#{p.id}</td>
                        <td className="cell-primary">{p.name ?? "-"}</td>
                        <td>{p.role_bucket ?? "-"}</td>
                        <td className="cell-mono">{p.contact_relevance_score ?? "-"}</td>
                        <td>{p.contact_relevance_bucket ?? "-"}</td>
                        <td>{p.dossier_status ?? "-"}</td>
                      </tr>
                    ))}
                    {companyProspects.length === 0 && (
                      <tr>
                        <td colSpan={6} style={{ textAlign: "center", color: "var(--ink-2)", padding: "24px", fontStyle: "italic" }}>
                          No prospects found
                        </td>
                      </tr>
                    )}
                  </tbody>
                </table>
              </div>
            </section>
          </>
        )}
      </Modal>
    </div>
  );
}
