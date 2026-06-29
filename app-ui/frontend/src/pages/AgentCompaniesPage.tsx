import { useEffect, useMemo, useState } from "react";
import { useParams } from "react-router-dom";
import { getCompanies, getCompanyDetail } from "../api/client";
import type { CompanyRow, ProspectRow } from "../api/types";
import { AppIcon, Modal, Pagination, Spinner } from "../components/index";

const PAGE_SIZE = 15;
const safe = (v: unknown) => (v == null || v === "" ? "—" : String(v));

type SortKey = "company_name" | "industry" | "prospect_count" | "research_status";
type SortDir = "asc" | "desc";

export function AgentCompaniesPage() {
  const { agentId } = useParams<{ agentId: string }>();
  const id = Number(agentId);

  const [rows, setRows]           = useState<CompanyRow[]>([]);
  const [search, setSearch]       = useState("");
  const [status, setStatus]       = useState("");
  const [loading, setLoading]     = useState(false);
  const [error, setError]         = useState("");
  const [page, setPage]           = useState(1);
  const [sortKey, setSortKey]     = useState<SortKey>("company_name");
  const [sortDir, setSortDir]     = useState<SortDir>("asc");

  const [selectedCompany, setSelectedCompany]   = useState<CompanyRow | null>(null);
  const [companyProspects, setCompanyProspects] = useState<ProspectRow[]>([]);
  const [detailLoading, setDetailLoading]       = useState(false);

  const load = async () => {
    setLoading(true);
    setError("");
    try {
      const r = await getCompanies({
        status: status || undefined,
        search: search || undefined,
        page_size: 200,
        agent_id: id,
      });
      setRows(r.items);
      setPage(1);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load companies");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { void load(); }, [agentId]);

  const openDetail = async (company: CompanyRow) => {
    setDetailLoading(true);
    setSelectedCompany(company);
    setCompanyProspects([]);
    try {
      const d = await getCompanyDetail(company.id, id);
      setSelectedCompany(d.company);
      setCompanyProspects(d.prospects);
    } catch {
      // keep selected company, no prospects
    } finally {
      setDetailLoading(false);
    }
  };

  const toggleSort = (key: SortKey) => {
    if (sortKey === key) setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    else { setSortKey(key); setSortDir("asc"); }
  };

  const sorted = useMemo(() => {
    return [...rows].sort((a, b) => {
     const av = (a[sortKey] ?? "");
      const bv = (b[sortKey] ?? "");
      const avN = typeof av === "string" ? av.toLowerCase() : av;
      const bvN = typeof bv === "string" ? bv.toLowerCase() : bv;
      if (avN < bvN) return sortDir === "asc" ? -1 : 1;
      if (avN > bvN) return sortDir === "asc" ? 1 : -1;
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
          <h1>Companies</h1>
          <p className="page-sub">Researched firms and firmographic intelligence.</p>
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
        {/* Toolbar */}
        <div className="grid-toolbar">
          <div className="grid-search">
            <AppIcon name="prospects" size={14} className="grid-search-icon" />
            <input
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && void load()}
              placeholder="Search company name, industry, location…"
            />
          </div>
          <select
            className="grid-filter-select"
            value={status}
            onChange={(e) => { setStatus(e.target.value); setTimeout(() => void load(), 0); }}
          >
            <option value="">All Statuses</option>
            <option value="completed">Completed</option>
            <option value="in_progress">In Progress</option>
            <option value="pending">Pending</option>
          </select>
          <button className="btn-primary" onClick={load} disabled={loading} style={{ flexShrink: 0 }}>
            Search
          </button>
          <span className="grid-count">{rows.length} companies</span>
        </div>

        {/* Table */}
        <div style={{ overflowX: "auto" }}>
          <table className="data-table">
            <thead>
              <tr>
                <th className={`sortable ${sortKey === "company_name" ? "sort-active" : ""}`} onClick={() => toggleSort("company_name")}>
                  Company <SortIcon col="company_name" />
                </th>
                <th className={`sortable ${sortKey === "industry" ? "sort-active" : ""}`} onClick={() => toggleSort("industry")}>
                  Industry <SortIcon col="industry" />
                </th>
                <th>Location</th>
                <th>Headcount</th>
                <th className={`sortable ${sortKey === "prospect_count" ? "sort-active" : ""}`} onClick={() => toggleSort("prospect_count")}>
                  Prospects <SortIcon col="prospect_count" />
                </th>
                <th className={`sortable ${sortKey === "research_status" ? "sort-active" : ""}`} onClick={() => toggleSort("research_status")}>
                  Status <SortIcon col="research_status" />
                </th>
                <th style={{ width: 60 }}></th>
              </tr>
            </thead>
            <tbody>
              {loading ? (
                <tr><td colSpan={7} className="loading-row"><Spinner size={16} /> &nbsp;Loading companies…</td></tr>
              ) : paged.length === 0 ? (
                <tr>
                  <td colSpan={7}>
                    <div className="empty-state" style={{ padding: "52px" }}>
                      <AppIcon name="companies" size={32} />
                      <p>No companies found. Adjust your filters or run the Research pipeline.</p>
                    </div>
                  </td>
                </tr>
              ) : (
                paged.map((row) => (
                  <tr key={row.id}>
                    <td className="cell-primary">{row.company_name}</td>
                    <td>{safe(row.industry)}</td>
                    <td>{safe(row.location)}</td>
                    <td className="cell-mono">{safe(row.employee_range)}</td>
                    <td style={{ fontWeight: 650 }}>{row.prospect_count ?? 0}</td>
                    <td>
                      <span className={`status-pill ${row.research_status ?? "pending"}`}>
                        {row.research_status ?? "pending"}
                      </span>
                    </td>
                    <td>
                      <div className="row-actions">
                        <button className="btn-icon" onClick={() => void openDetail(row)} title="View details">
                          <AppIcon name="eye" size={13} />
                        </button>
                        {row.linkedin_url && (
                          <a
                            href={row.linkedin_url}
                            target="_blank"
                            rel="noreferrer"
                            className="btn-icon"
                            title="View LinkedIn"
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

        {/* Footer */}
        <div className="grid-footer">
          <Pagination page={page} pageSize={PAGE_SIZE} totalItems={sorted.length} onPageChange={setPage} />
        </div>
      </div>

      {/* Company Detail Modal */}
      <Modal
        open={!!selectedCompany}
        onClose={() => { setSelectedCompany(null); setCompanyProspects([]); }}
        title={selectedCompany?.company_name ?? "Company Details"}
        size="xl"
      >
        {selectedCompany && (
          <div style={{ display: "flex", flexDirection: "column", gap: 24 }}>
            {/* Firmographics */}
            <div>
              <div className="form-label" style={{ marginBottom: 12 }}>Firmographics</div>
              <div className="kv-grid">
                <div className="kv-item">
                  <span className="kv-label">Industry</span>
                  <span className="kv-value">{safe(selectedCompany.industry)}</span>
                </div>
                <div className="kv-item">
                  <span className="kv-label">Headcount</span>
                  <span className="kv-value">{safe(selectedCompany.employee_range)}</span>
                </div>
                <div className="kv-item">
                  <span className="kv-label">Location</span>
                  <span className="kv-value">{safe(selectedCompany.location)}</span>
                </div>
                <div className="kv-item">
                  <span className="kv-label">Followers</span>
                  <span className="kv-value">{safe(selectedCompany.followers)}</span>
                </div>
                <div className="kv-item">
                  <span className="kv-label">Research Status</span>
                  <span className={`status-pill ${selectedCompany.research_status ?? "pending"}`}>
                    {selectedCompany.research_status ?? "pending"}
                  </span>
                </div>
                <div className="kv-item">
                  <span className="kv-label">LinkedIn</span>
                  {selectedCompany.linkedin_url ? (
                    <a href={selectedCompany.linkedin_url} target="_blank" rel="noreferrer" style={{ color: "var(--blue)", fontWeight: 600, fontSize: "0.84rem" }}>
                      View profile ↗
                    </a>
                  ) : (
                    <span className="kv-value">—</span>
                  )}
                </div>
              </div>
              {selectedCompany.tagline && (
                <div className="kv-item" style={{ marginTop: 12 }}>
                  <span className="kv-label">Tagline</span>
                  <span style={{ fontSize: "0.84rem", color: "var(--ink-1)", fontStyle: "italic", marginTop: 3, display: "block" }}>
                    "{selectedCompany.tagline}"
                  </span>
                </div>
              )}
            </div>

            {/* Prospects sub-table */}
            <div>
              <div className="form-label" style={{ marginBottom: 10 }}>
                Identified Prospects {!detailLoading && `(${companyProspects.length})`}
              </div>
              {detailLoading ? (
                <div style={{ display: "flex", alignItems: "center", gap: 8, color: "var(--ink-3)", fontSize: "0.84rem", padding: "16px 0" }}>
                  <Spinner size={14} /> Loading prospects…
                </div>
              ) : companyProspects.length === 0 ? (
                <div style={{ padding: "20px", background: "var(--bg-subtle)", borderRadius: "var(--r-md)", color: "var(--ink-3)", fontSize: "0.84rem", textAlign: "center" }}>
                  No prospects identified for this company yet.
                </div>
              ) : (
                <div style={{ border: "1px solid var(--border)", borderRadius: "var(--r-md)", overflow: "hidden" }}>
                  <table className="data-table" style={{ fontSize: "0.82rem" }}>
                    <thead>
                      <tr>
                        <th>Name</th>
                        <th>Title</th>
                        <th>Bucket</th>
                        <th>Score</th>
                        <th>Outreach</th>
                      </tr>
                    </thead>
                    <tbody>
                      {companyProspects.map((p) => {
                        const score = p.contact_relevance_score ?? 0;
                        const fillClass = score >= 70 ? "high" : score >= 40 ? "mid" : "low";
                        return (
                          <tr key={p.id}>
                            <td className="cell-primary">{p.name ?? "—"}</td>
                            <td style={{ fontSize: "0.78rem" }}>{p.current_title ?? "—"}</td>
                            <td>
                              <span className={`status-pill ${p.contact_relevance_bucket ?? "pending"}`}>
                                {p.contact_relevance_bucket ?? "—"}
                              </span>
                            </td>
                            <td>
                              <div className="score-bar">
                                <div className="score-track">
                                  <div className={`score-fill ${fillClass}`} style={{ width: `${score}%` }} />
                                </div>
                                <span className="score-num">{score}%</span>
                              </div>
                            </td>
                            <td>
                              <span className={`status-pill ${p.outreach_dispatch_status ?? "pending"}`}>
                                {p.outreach_dispatch_status ?? "unsent"}
                              </span>
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          </div>
        )}
      </Modal>
    </div>
  );
}
