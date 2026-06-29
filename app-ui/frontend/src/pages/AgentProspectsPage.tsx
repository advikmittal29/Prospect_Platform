import { useEffect, useMemo, useState } from "react";
import { useParams } from "react-router-dom";
import { getProspectDetail, getProspects } from "../api/client";
import type { ProspectDetail, ProspectRow } from "../api/types";
import { AppIcon, Modal, Pagination, Spinner } from "../components/index";

const PAGE_SIZE = 15;
const safe = (v: unknown): string => {
  if (v == null || v === "") return "—";
  if (typeof v === "string" || typeof v === "number" || typeof v === "boolean") return String(v);
  return "—";
};

function tryParseJson(v: unknown): any {
  if (typeof v !== "string") return v;
  try { return JSON.parse(v); } catch { return v; }
}

type SortKey = "name" | "current_title" | "company_name" | "contact_relevance_score" | "outreach_dispatch_status";
type SortDir = "asc" | "desc";
type DetailTab = "profile" | "assessment" | "dossier" | "outreach";

export function AgentProspectsPage() {
  const { agentId } = useParams<{ agentId: string }>();
  const id = Number(agentId);

  const [rows, setRows]       = useState<ProspectRow[]>([]);
  const [search, setSearch]   = useState("");
  const [bucket, setBucket]   = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError]     = useState("");
  const [page, setPage]       = useState(1);
  const [sortKey, setSortKey] = useState<SortKey>("contact_relevance_score");
  const [sortDir, setSortDir] = useState<SortDir>("desc");

  const [detail, setDetail]           = useState<ProspectDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [activeTab, setActiveTab]     = useState<DetailTab>("profile");

  const load = async () => {
    setLoading(true);
    setError("");
    try {
      const r = await getProspects({ search: search || undefined, bucket: bucket || undefined, page_size: 200, agent_id: id });
      setRows(r.items);
      setPage(1);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load prospects");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { void load(); }, [agentId]);

  const openDetail = async (row: ProspectRow) => {
    setDetail(row as ProspectDetail);
    setActiveTab("profile");
    setDetailLoading(true);
    try {
      setDetail(await getProspectDetail(row.id, id));
    } catch {
      // use basic row data
    } finally {
      setDetailLoading(false);
    }
  };

  const toggleSort = (key: SortKey) => {
    if (sortKey === key) setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    else { setSortKey(key); setSortDir(key === "contact_relevance_score" ? "desc" : "asc"); }
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

  const experiences = tryParseJson(detail?.experiences_json ?? detail?.experiences);
  const assessment  = tryParseJson(detail?.llm_assessment_json ?? detail?.llm_assessment);
  const dossier     = tryParseJson(detail?.dossier_json ?? detail?.dossier);

  return (
    <div className="page-stack fade-in">
      <div className="page-header">
        <div>
          <h1>Prospects</h1>
          <p className="page-sub">High-intent individuals identified through cross-channel research.</p>
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
            <AppIcon name="prospects" size={14} className="grid-search-icon" />
            <input
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && void load()}
              placeholder="Search by name, title, or company…"
            />
          </div>
          <select
            className="grid-filter-select"
            value={bucket}
            onChange={(e) => { setBucket(e.target.value); setTimeout(() => void load(), 0); }}
          >
            <option value="">All Buckets</option>
            <option value="prime">Prime</option>
            <option value="strong">Strong</option>
            <option value="moderate">Moderate</option>
            <option value="weak">Weak</option>
          </select>
          <button className="btn-primary" onClick={load} disabled={loading} style={{ flexShrink: 0 }}>
            Search
          </button>
          <span className="grid-count">{rows.length} prospects</span>
        </div>

        <div style={{ overflowX: "auto" }}>
          <table className="data-table">
            <thead>
              <tr>
                <th className={`sortable ${sortKey === "name" ? "sort-active" : ""}`} onClick={() => toggleSort("name")}>
                  Name <SortIcon col="name" />
                </th>
                <th className={`sortable ${sortKey === "current_title" ? "sort-active" : ""}`} onClick={() => toggleSort("current_title")}>
                  Title <SortIcon col="current_title" />
                </th>
                <th className={`sortable ${sortKey === "company_name" ? "sort-active" : ""}`} onClick={() => toggleSort("company_name")}>
                  Company <SortIcon col="company_name" />
                </th>
                <th className={`sortable ${sortKey === "contact_relevance_score" ? "sort-active" : ""}`} onClick={() => toggleSort("contact_relevance_score")}>
                  Relevance <SortIcon col="contact_relevance_score" />
                </th>
                <th className={`sortable ${sortKey === "outreach_dispatch_status" ? "sort-active" : ""}`} onClick={() => toggleSort("outreach_dispatch_status")}>
                  Outreach <SortIcon col="outreach_dispatch_status" />
                </th>
                <th style={{ width: 60 }}></th>
              </tr>
            </thead>
            <tbody>
              {loading ? (
                <tr><td colSpan={6} className="loading-row"><Spinner size={16} /> &nbsp;Loading prospects…</td></tr>
              ) : paged.length === 0 ? (
                <tr>
                  <td colSpan={6}>
                    <div className="empty-state" style={{ padding: "52px" }}>
                      <AppIcon name="prospects" size={32} />
                      <p>No prospects found. Run the Intelligence pipeline to generate prospect data.</p>
                    </div>
                  </td>
                </tr>
              ) : (
                paged.map((row) => {
                  const score = row.contact_relevance_score ?? 0;
                  const fillClass = score >= 70 ? "high" : score >= 40 ? "mid" : "low";
                  return (
                    <tr key={row.id}>
                      <td className="cell-primary">{row.name ?? "—"}</td>
                      <td style={{ fontSize: "0.8rem" }}>{row.current_title ?? row.headline ?? "—"}</td>
                      <td className="cell-accent">{row.company_name ?? "—"}</td>
                      <td style={{ minWidth: 140 }}>
                        <div className="score-bar">
                          <div className="score-track">
                            <div className={`score-fill ${fillClass}`} style={{ width: `${score}%` }} />
                          </div>
                          <span className="score-num">{score}%</span>
                        </div>
                      </td>
                      <td>
                        <span className={`status-pill ${row.outreach_dispatch_status ?? "pending"}`}>
                          {row.outreach_dispatch_status ?? "unsent"}
                        </span>
                      </td>
                      <td>
                        <div className="row-actions">
                          <button className="btn-icon" onClick={() => void openDetail(row)} title="View dossier">
                            <AppIcon name="eye" size={13} />
                          </button>
                          {row.linkedin_profile_url && (
                            <a
                              href={row.linkedin_profile_url}
                              target="_blank"
                              rel="noreferrer"
                              className="btn-icon"
                              title="LinkedIn profile"
                            >
                              <AppIcon name="externalLink" size={13} />
                            </a>
                          )}
                        </div>
                      </td>
                    </tr>
                  );
                })
              )}
            </tbody>
          </table>
        </div>

        <div className="grid-footer">
          <Pagination page={page} pageSize={PAGE_SIZE} totalItems={sorted.length} onPageChange={setPage} />
        </div>
      </div>

      {/* Prospect Detail Modal */}
      <Modal
        open={!!detail}
        onClose={() => setDetail(null)}
        title={detail?.name ?? "Prospect Detail"}
        size="xl"
      >
        {detail && (
          <div style={{ display: "flex", flexDirection: "column", gap: 0 }}>
            {/* Hero */}
            <div style={{
              display: "flex", alignItems: "center", gap: 20,
              paddingBottom: 20, marginBottom: 0,
              borderBottom: "1px solid var(--border)",
            }}>
              <div style={{
                width: 64, height: 64, borderRadius: 16,
                background: "var(--blue)", color: "white",
                display: "flex", alignItems: "center", justifyContent: "center",
                fontSize: "1.8rem", fontWeight: 800, flexShrink: 0,
              }}>
                {detail.name?.[0]?.toUpperCase() ?? "?"}
              </div>
              <div style={{ flex: 1, minWidth: 0 }}>
                <h2 style={{ fontSize: "1.2rem", marginBottom: 4 }}>{detail.name}</h2>
                <p style={{ fontSize: "0.88rem", color: "var(--ink-2)", marginBottom: 8 }}>
                  {detail.current_title ?? detail.headline} {detail.current_company ? `· ${detail.current_company}` : ""}
                </p>
                <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                  {detail.role_bucket && (
                    <span className="status-pill active">{detail.role_bucket}</span>
                  )}
                  {detail.contact_relevance_score != null && (
                    <span style={{ fontSize: "0.75rem", fontWeight: 700, padding: "2px 8px", borderRadius: "var(--r-full)", background: "var(--blue-soft)", color: "var(--blue)" }}>
                      {detail.contact_relevance_score}% relevance
                    </span>
                  )}
                </div>
              </div>
              <div style={{ display: "flex", gap: 8, flexShrink: 0 }}>
                {detail.linkedin_profile_url && (
                  <a href={detail.linkedin_profile_url} target="_blank" rel="noreferrer" className="btn-secondary">
                    <AppIcon name="linkedin" size={13} /> LinkedIn
                  </a>
                )}
              </div>
            </div>

            {/* Tabs */}
            <div className="detail-tabs" style={{ margin: "0 -24px", padding: "0 24px" }}>
              {(["profile", "assessment", "dossier", "outreach"] as DetailTab[]).map((tab) => (
                <button
                  key={tab}
                  className={`detail-tab-btn ${activeTab === tab ? "active" : ""}`}
                  onClick={() => setActiveTab(tab)}
                >
                  {tab.charAt(0).toUpperCase() + tab.slice(1)}
                </button>
              ))}
            </div>

            {detailLoading ? (
              <div style={{ display: "flex", alignItems: "center", gap: 10, padding: "32px 0", color: "var(--ink-3)" }}>
                <Spinner size={16} /> Loading full profile…
              </div>
            ) : (
              <div style={{ paddingTop: 24 }}>
                {/* Profile tab */}
                {activeTab === "profile" && (
                  <div style={{ display: "flex", flexDirection: "column", gap: 20 }}>
                    <div>
                      <div className="form-label" style={{ marginBottom: 8 }}>About</div>
                      <p style={{ fontSize: "0.88rem", lineHeight: 1.65, color: "var(--ink-1)" }}>
                        {detail.profile_summary_text ?? detail.about_text ?? "No summary available."}
                      </p>
                    </div>
                    {Array.isArray(experiences) && experiences.length > 0 && (
                      <div>
                        <div className="form-label" style={{ marginBottom: 10 }}>Experience</div>
                        <div className="exp-list">
                          {experiences.map((exp: any, i: number) => (
                            <div key={i} className="exp-item">
                              <span className="exp-title">{exp.title ?? exp.role ?? "—"}</span>
                              <span className="exp-company">{exp.company ?? exp.company_name ?? "—"}</span>
                              <span className="exp-date">{exp.duration ?? exp.date_range ?? "—"}</span>
                            </div>
                          ))}
                        </div>
                      </div>
                    )}
                  </div>
                )}

                {/* Assessment tab */}
                {activeTab === "assessment" && (
                  <div>
                    {assessment && typeof assessment === "object" && Object.keys(assessment).length > 0 ? (
                      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 14 }}>
                        {Object.entries(assessment).map(([k, v]: any) => (
                          <div key={k} style={{ padding: "12px", border: "1px solid var(--border)", borderRadius: "var(--r-md)" }}>
                            <div className="form-label" style={{ marginBottom: 4 }}>{k.replace(/_/g, " ")}</div>
                            <p style={{ fontSize: "0.84rem", color: "var(--ink-1)", fontWeight: 500 }}>{safe(v)}</p>
                          </div>
                        ))}
                      </div>
                    ) : (
                      <div className="empty-state"><p>No assessment data available yet.</p></div>
                    )}
                  </div>
                )}

                {/* Dossier tab */}
                {activeTab === "dossier" && (
                  <div>
                    {dossier ? (
                      <pre style={{
                        background: "var(--bg-subtle)", padding: "16px",
                        borderRadius: "var(--r-md)", fontFamily: "var(--font-mono)",
                        fontSize: "0.75rem", lineHeight: 1.65, overflow: "auto",
                        maxHeight: 400, color: "var(--ink-1)",
                        border: "1px solid var(--border)",
                      }}>
                        {JSON.stringify(dossier, null, 2)}
                      </pre>
                    ) : (
                      <div className="empty-state"><p>Dossier not yet generated for this prospect.</p></div>
                    )}
                  </div>
                )}

                {/* Outreach tab */}
                {activeTab === "outreach" && (
                  <div style={{ display: "flex", flexDirection: "column", gap: 20 }}>
                    <div style={{ display: "flex", gap: 32 }}>
                      <div className="kv-item">
                        <span className="kv-label">Dispatch Status</span>
                        <span className={`status-pill ${detail.outreach_dispatch_status ?? "pending"}`}>
                          {detail.outreach_dispatch_status ?? "unsent"}
                        </span>
                      </div>
                      <div className="kv-item">
                        <span className="kv-label">Channel</span>
                        <span className="kv-value">{detail.outreach_dispatch_channel ?? "LinkedIn"}</span>
                      </div>
                      {detail.outreach_sent_at_utc && (
                        <div className="kv-item">
                          <span className="kv-label">Sent At</span>
                          <span className="kv-value">{new Date(detail.outreach_sent_at_utc).toLocaleString()}</span>
                        </div>
                      )}
                    </div>
                    <div>
                      <div className="form-label" style={{ marginBottom: 8 }}>Generated Message</div>
                      {detail.outreach_message ? (
                        <div style={{
                          background: "var(--bg-subtle)", padding: "16px 20px",
                          borderRadius: "var(--r-lg)", lineHeight: 1.7,
                          fontSize: "0.88rem", whiteSpace: "pre-wrap",
                          border: "1px solid var(--border)", color: "var(--ink-1)",
                        }}>
                          {detail.outreach_message}
                        </div>
                      ) : (
                        <div className="empty-state" style={{ padding: "24px", background: "var(--bg-subtle)", borderRadius: "var(--r-md)" }}>
                          <p>No outreach message generated yet. Run the Intelligence pipeline.</p>
                        </div>
                      )}
                    </div>
                  </div>
                )}
              </div>
            )}
          </div>
        )}
      </Modal>
    </div>
  );
}
