import { FormEvent, type CSSProperties, useEffect, useMemo, useState } from "react";
import { useOutletContext } from "react-router-dom";
import { getProspectDetail, getProspects } from "../api/client";
import type { ProspectDetail, ProspectRow } from "../api/types";
import type { AgentScopeContextValue } from "../agentScope";
import { Modal } from "../components/Modal";
import { Pagination } from "../components/Pagination";
import { SectionCard } from "../components/SectionCard";

function parseJ(v: unknown): unknown {
  if (typeof v !== "string") return v;
  try {
    return JSON.parse(v);
  } catch {
    return v;
  }
}

function safe(v: unknown): string {
  if (v == null || v === "") return "Not available";
  if (typeof v === "string" || typeof v === "number" || typeof v === "boolean") return String(v);
  if (Array.isArray(v)) return v.slice(0, 3).map(safe).join(" | ");
  const r = v as Record<string, unknown>;
  return Object.entries(r).slice(0, 3).map(([k, x]) => `${k}: ${safe(x)}`).join(" | ") || "Structured data";
}

function humanize(k: string) {
  return k.replace(/_/g, " ").replace(/([a-z])([A-Z])/g, "$1 $2").replace(/^./, (c) => c.toUpperCase());
}

function flatten(v: unknown, prefix = "", depth = 0, out: { label: string; value: string }[] = []): { label: string; value: string }[] {
  if (out.length >= 12 || depth > 2 || v == null) return out;
  if (Array.isArray(v)) {
    if (!v.length) return out;
    if (typeof v[0] !== "object") {
      out.push({ label: prefix || "Items", value: v.slice(0, 4).map(safe).join(" | ") });
      return out;
    }
    v.slice(0, 3).forEach((item, i) => flatten(item, `${prefix || "Item"} ${i + 1}`, depth + 1, out));
    return out;
  }
  if (typeof v === "object") {
    Object.entries(v as Record<string, unknown>).slice(0, 8).forEach(([k, val]) => {
      const label = prefix ? `${prefix} / ${humanize(k)}` : humanize(k);
      if (val != null && typeof val === "object") flatten(val, label, depth + 1, out);
      else out.push({ label, value: safe(val) });
    });
    return out;
  }
  out.push({ label: prefix || "Value", value: safe(v) });
  return out;
}

const bucketStyle: Record<string, CSSProperties> = {
  prime: { color: "var(--success)", fontWeight: 700 },
  strong: { color: "var(--blue-text)", fontWeight: 600 },
  moderate: { color: "var(--warning)", fontWeight: 600 },
  weak: { color: "var(--ink-2)" },
};

export function ProspectsPage() {
  const { activeAgentId } = useOutletContext<AgentScopeContextValue>();
  const [rows, setRows] = useState<ProspectRow[]>([]);
  const [search, setSearch] = useState("");
  const [bucket, setBucket] = useState("");
  const [companyId, setCompanyId] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [page, setPage] = useState(1);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [detail, setDetail] = useState<ProspectDetail | null>(null);
  const PAGE_SIZE = 50;
  const [total, setTotal] = useState(0);

  const load = async () => {
    setLoading(true);
    setError("");
    try {
      const r = await getProspects({
        search: search || undefined,
        bucket: bucket || undefined,
        company_id: companyId ? Number(companyId) : undefined,
        page,
        page_size: PAGE_SIZE,
        agent_id: activeAgentId,
      });
      setRows(r.items);
      setTotal(r.total);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load prospects");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void load();
  }, [page, search, bucket, companyId, activeAgentId]);

  const onFilter = (e: FormEvent) => {
    e.preventDefault();
    setPage(1);
  };

  const openDetail = async (id: number) => {
    setSelectedId(id);
    try {
      setDetail(await getProspectDetail(id, activeAgentId));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load");
    }
  };

  const pagedRows = useMemo(() => rows.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE), [rows, page]);

  const exps = useMemo(() => {
    if (!detail) return [];
    const s = detail.experiences ?? parseJ(detail.experiences_json);
    return Array.isArray(s) ? s.filter((x): x is Record<string, unknown> => typeof x === "object" && x !== null) : [];
  }, [detail]);
  const posts = useMemo(() => {
    if (!detail) return [];
    const s = detail.recent_posts ?? parseJ(detail.recent_posts_json);
    return Array.isArray(s) ? s.map(safe).filter((x) => x !== "Not available").slice(0, 5) : [];
  }, [detail]);
  const assess = useMemo(() => {
    if (!detail) return [];
    return flatten(parseJ(detail.llm_assessment ?? detail.llm_assessment_json));
  }, [detail]);
  const dossier = useMemo(() => {
    if (!detail) return [];
    return flatten(parseJ(detail.dossier ?? detail.dossier_json));
  }, [detail]);
  const outreach = useMemo(() => {
    if (!detail) return [];
    return flatten(parseJ(detail.outreach_message_json ?? detail.outreach_message));
  }, [detail]);

  return (
    <div className="page-stack">
      <div className="page-header fade-in">
        <div className="page-header-left">
          <h1>Prospect Intelligence</h1>
          <p className="page-sub">
            360 view of each prospect: profile, assessment, dossier, and outreach context.
            {activeAgentId ? ` (Agent #${activeAgentId})` : ""}
          </p>
        </div>
      </div>

      <SectionCard title="Search and Filter" subtitle="Find high-intent prospects and inspect scoring">
        <form className="filter-grid" onSubmit={onFilter}>
          <label>
            Search
            <input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="name / headline / company" />
          </label>
          <label>
            Relevance Bucket
            <select value={bucket} onChange={(e) => setBucket(e.target.value)}>
              <option value="">All buckets</option>
              <option value="prime">Prime</option>
              <option value="strong">Strong</option>
              <option value="moderate">Moderate</option>
              <option value="weak">Weak</option>
            </select>
          </label>
          <label>
            Company ID
            <input value={companyId} onChange={(e) => setCompanyId(e.target.value)} placeholder="numeric ID" />
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

      <SectionCard title="Prospect Results" subtitle={`${total} prospects found`} noPad>
        <div className="table-wrap fixed-height" style={{ border: "none", borderRadius: 0 }}>
          <table>
            <thead>
              <tr><th>ID</th><th>Name</th><th>Company</th><th>Title</th><th>Role Bucket</th><th>Score</th><th>Bucket</th><th>Dispatch</th><th></th></tr>
            </thead>
            <tbody>
              {pagedRows.map((row) => (
                <tr key={row.id} className={selectedId === row.id ? "row-active" : ""}>
                  <td className="cell-mono">#{row.id}</td>
                  <td className="cell-primary">{row.name ?? "-"}</td>
                  <td>{row.company_name ?? "-"}</td>
                  <td>{row.current_title ?? row.headline ?? "-"}</td>
                  <td>{row.role_bucket ?? "-"}</td>
                  <td className="cell-mono">{row.contact_relevance_score ?? "-"}</td>
                  <td>{row.contact_relevance_bucket ? <span style={{ fontSize: "0.78rem", textTransform: "capitalize", ...(bucketStyle[row.contact_relevance_bucket] ?? {}) }}>{row.contact_relevance_bucket}</span> : "-"}</td>
                  <td style={{ fontSize: "0.78rem" }}>
                    {row.outreach_dispatch_status ?? "-"}
                    {row.outreach_dispatch_channel ? ` (${row.outreach_dispatch_channel})` : ""}
                  </td>
                  <td><button className="btn-ghost btn-sm" onClick={() => void openDetail(row.id)}>View</button></td>
                </tr>
              ))}
              {pagedRows.length === 0 && (
                <tr>
                  <td colSpan={9} style={{ textAlign: "center", color: "var(--ink-2)", padding: "40px", fontStyle: "italic" }}>
                    No prospects found. Try adjusting filters.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
        <Pagination page={page} pageSize={PAGE_SIZE} totalItems={total} onPageChange={setPage} />
      </SectionCard>

      <Modal open={!!detail} onClose={() => { setDetail(null); setSelectedId(null); }} title={detail?.name ?? "Prospect Detail"} subtitle="Profile, assessments and outreach context" size="xl">
        {detail && (
          <>
            <div className="info-grid">
              <article className="info-card"><h4>Headline</h4><p>{safe(detail.headline)}</p></article>
              <article className="info-card"><h4>Current Title</h4><p>{safe(detail.current_title)}</p></article>
              <article className="info-card"><h4>Current Company</h4><p>{safe(detail.current_company)}</p></article>
              <article className="info-card"><h4>Role Bucket</h4><p>{safe(detail.role_bucket)}</p></article>
              <article className="info-card"><h4>Relevance</h4><p>{safe(detail.contact_relevance_score)} ({safe(detail.contact_relevance_bucket)})</p></article>
              <article className="info-card"><h4>Company Match</h4><p>{safe(detail.company_match_confidence)}</p></article>
              <article className="info-card"><h4>Dispatch Status</h4><p>{safe(detail.outreach_dispatch_status)}</p></article>
              <article className="info-card"><h4>Dispatch Channel</h4><p>{safe(detail.outreach_dispatch_channel)}</p></article>
              <article className="info-card"><h4>Sent At</h4><p>{safe(detail.outreach_sent_at_utc)}</p></article>
            </div>
            <div className="button-row">
              {detail.linkedin_profile_url && <a className="btn-primary" href={detail.linkedin_profile_url} target="_blank" rel="noreferrer">LinkedIn Profile</a>}
              {detail.dossier_status && <span className="badge">Dossier: {detail.dossier_status}</span>}
            </div>
            <section>
              <h3>Profile Summary</h3>
              <article className="list-card"><p className="list-body">{safe(detail.profile_summary_text ?? detail.about_text)}</p></article>
            </section>
            <section>
              <h3>Experience</h3>
              {exps.length ? exps.map((e, i) => (
                <article key={i} className="list-card">
                  <p className="list-title">{safe(e.title ?? e.role ?? e.position ?? `Experience ${i + 1}`)}</p>
                  <p className="list-body">{safe(e.company ?? e.organization ?? "-")}{e.duration ? ` | ${safe(e.duration)}` : ""}</p>
                  {e.description ? <p className="list-body" style={{ marginTop: 4 }}>{safe(e.description)}</p> : null}
                </article>
              )) : <p className="muted">No experience entries captured.</p>}
            </section>
            <section>
              <h3>Recent Posts</h3>
              {posts.length ? posts.map((post, i) => (
                <article key={i} className="list-card"><p className="list-body">{post}</p></article>
              )) : <p className="muted">No recent posts captured.</p>}
            </section>
            <section>
              <h3>Assessment Highlights</h3>
              {assess.length ? <div className="kv-grid">{assess.map((e, i) => <article key={i} className="kv-item"><p className="kv-label">{e.label}</p><p className="kv-value">{e.value}</p></article>)}</div> : <p className="muted">No assessment breakdown available.</p>}
            </section>
            <section>
              <h3>Dossier Highlights</h3>
              {dossier.length ? <div className="kv-grid">{dossier.map((e, i) => <article key={i} className="kv-item"><p className="kv-label">{e.label}</p><p className="kv-value">{e.value}</p></article>)}</div> : <p className="muted">Dossier not yet generated.</p>}
            </section>
            <section>
              <h3>Outreach Preview</h3>
              {outreach.length ? <div className="kv-grid">{outreach.map((e, i) => <article key={i} className="kv-item"><p className="kv-label">{e.label}</p><p className="kv-value">{e.value}</p></article>)}</div> : <p className="muted">Outreach message not generated yet.</p>}
            </section>
          </>
        )}
      </Modal>
    </div>
  );
}
