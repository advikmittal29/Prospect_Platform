import { useEffect, useMemo, useState } from "react";
import { useParams } from "react-router-dom";
import {
  checkProspectMessagesNow,
  generateProspectMessage,
  getProspectDetail,
  getProspects,
  sendProspectMessage,
  updateProspectMessage,
} from "../api/client";
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

const REMARKS: Record<string, { label: string; fg: string; bg: string }> = {
  email_sent:     { label: "Email sent to manager", fg: "#166534", bg: "#dcfce7" },
  handed_off:     { label: "Handed off",            fg: "#166534", bg: "#dcfce7" },
  meeting_booked: { label: "Meeting booked",        fg: "#1e40af", bg: "#dbeafe" },
  talking:        { label: "Talking",               fg: "#1e40af", bg: "#dbeafe" },
  in_process:     { label: "In process",            fg: "#92400e", bg: "#fef3c7" },
  not_interested: { label: "Not interested",        fg: "#991b1b", bg: "#fee2e2" },
  closed:         { label: "Closed",                fg: "#374151", bg: "#f3f4f6" },
  not_contacted:  { label: "Not contacted",         fg: "#6b7280", bg: "#f3f4f6" },
};

function RemarkPill({ status }: { status?: string | null }) {
  const key = status ?? "not_contacted";
  // An unrecognized status renders as its raw value in neutral grey rather
  // than silently masquerading as "Not contacted".
  const r = REMARKS[key] ?? { label: key.replace(/_/g, " "), fg: "#374151", bg: "#f3f4f6" };
  return (
    <span style={{
      fontSize: "0.72rem", fontWeight: 700, padding: "3px 9px",
      borderRadius: 999, background: r.bg, color: r.fg, whiteSpace: "nowrap",
    }}>
      {r.label}
    </span>
  );
}

type SortKey = "name" | "current_title" | "company_name" | "contact_relevance_score" | "outreach_dispatch_status" | "engagement_status";
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
  const [checkingMessages, setCheckingMessages] = useState(false);
  const [checkResult, setCheckResult] = useState<{ ok: boolean; text: string } | null>(null);

  // Outreach message editing + manual send
  const [messageDraft, setMessageDraft] = useState("");
  const [savingMessage, setSavingMessage] = useState(false);
  const [sendResult, setSendResult] = useState<{ ok: boolean; text: string } | null>(null);
  // The pending send awaiting confirmation. `message` is the exact text that
  // will go out, so the dialog can never promise something different.
  const [confirmSend, setConfirmSend] = useState<
    { id: number; name: string; message: string; dirty: boolean } | null
  >(null);
  const [confirmLoading, setConfirmLoading] = useState(false);
  const [sending, setSending] = useState(false);
  // Per-prospect message generation (id currently generating, from row or modal)
  const [generatingId, setGeneratingId] = useState<number | null>(null);

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
    setCheckResult(null);
    setSendResult(null);
    setMessageDraft("");
    setDetailLoading(true);
    try {
      const d = await getProspectDetail(row.id, id);
      setDetail(d);
      setMessageDraft(d.outreach_message ?? "");
    } catch {
      // use basic row data
    } finally {
      setDetailLoading(false);
    }
  };

  const draftDirty = !!detail && messageDraft !== (detail.outreach_message ?? "");

  /** Generate (or regenerate) the AI outreach message for one prospect. Used by
   *  the row lightning button (when no message exists yet) and the modal. */
  const handleGenerate = async (prospectId: number) => {
    setGeneratingId(prospectId);
    setSendResult(null);
    try {
      const res = await generateProspectMessage(prospectId);
      setRows((rs) =>
        rs.map((r) => (r.id === prospectId ? { ...r, has_outreach_message: true } : r))
      );
      if (detail?.id === prospectId) {
        setDetail({ ...detail, outreach_message: res.message });
        setMessageDraft(res.message);
      }
      setSendResult({ ok: true, text: "Message generated." });
    } catch (err) {
      setSendResult({ ok: false, text: err instanceof Error ? err.message : "Generation failed" });
    } finally {
      setGeneratingId(null);
    }
  };

  const handleSaveMessage = async () => {
    if (!detail) return;
    setSavingMessage(true);
    setSendResult(null);
    try {
      await updateProspectMessage(detail.id, messageDraft);
      setDetail({ ...detail, outreach_message: messageDraft });
      setRows((rs) =>
        rs.map((r) =>
          r.id === detail.id ? { ...r, has_outreach_message: messageDraft.trim().length > 0 } : r
        )
      );
      setSendResult({ ok: true, text: "Message saved." });
    } catch (err) {
      setSendResult({ ok: false, text: err instanceof Error ? err.message : "Save failed" });
    } finally {
      setSavingMessage(false);
    }
  };

  /** Opens the confirm dialog for a row — pulls the stored message so the
   *  dialog shows exactly what will be sent rather than a blind "Send?". */
  const requestSendFromRow = async (row: ProspectRow) => {
    setSendResult(null);
    setConfirmLoading(true);
    setConfirmSend({ id: row.id, name: row.name ?? "this prospect", message: "", dirty: false });
    try {
      const d = await getProspectDetail(row.id, id);
      setConfirmSend({
        id: row.id,
        name: d.name ?? row.name ?? "this prospect",
        message: d.outreach_message ?? "",
        dirty: false,
      });
    } catch {
      // Leave the dialog open without a preview rather than dropping the intent.
    } finally {
      setConfirmLoading(false);
    }
  };

  const requestSendFromModal = () => {
    if (!detail) return;
    setSendResult(null);
    setConfirmSend({
      id: detail.id,
      name: detail.name ?? "this prospect",
      message: messageDraft,
      dirty: draftDirty,
    });
  };

  const handleConfirmedSend = async () => {
    if (!confirmSend) return;
    const { id: pid, message, dirty } = confirmSend;
    setSending(true);
    try {
      // The send endpoint reads the STORED message, so an unsaved edit would
      // otherwise send the old text — persist the draft first so what the
      // dialog previewed is what actually goes out.
      if (dirty) {
        await updateProspectMessage(pid, message);
        if (detail?.id === pid) setDetail({ ...detail, outreach_message: message });
      }
      const res = await sendProspectMessage(pid);
      setSendResult({ ok: true, text: res.message || "Message sent." });
      setConfirmSend(null);
      await load();
      if (detail?.id === pid) {
        try {
          const d = await getProspectDetail(pid, id);
          setDetail(d);
          setMessageDraft(d.outreach_message ?? "");
        } catch {
          // keep the existing detail
        }
      }
    } catch (err) {
      setSendResult({ ok: false, text: err instanceof Error ? err.message : "Send failed" });
      setConfirmSend(null);
    } finally {
      setSending(false);
    }
  };

  const handleCheckMessagesNow = async () => {
    if (!detail) return;
    setCheckingMessages(true);
    setCheckResult(null);
    try {
      const result = await checkProspectMessagesNow(detail.id);
      setCheckResult({
        ok: result.ok,
        text: result.ok ? "Checked — up to date." : "Check completed with errors — see Run History for details.",
      });
    } catch (err) {
      setCheckResult({ ok: false, text: err instanceof Error ? err.message : "Check failed" });
    } finally {
      setCheckingMessages(false);
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

        {/* Send outcome for list-triggered sends (the modal shows its own copy) */}
        {sendResult && !detail && (
          <div
            className={sendResult.ok ? "success-banner" : "error-banner"}
            style={{
              margin: "0 16px 12px", borderRadius: "var(--r-md)", padding: "10px 14px",
              fontSize: "0.82rem", fontWeight: 600,
              display: "flex", alignItems: "center", gap: 8,
              color: sendResult.ok ? "var(--success)" : "var(--danger, #dc2626)",
            }}
          >
            <AppIcon name={sendResult.ok ? "check" : "close"} size={13} />
            {sendResult.text}
            <button
              className="btn-icon"
              style={{ marginLeft: "auto" }}
              onClick={() => setSendResult(null)}
              title="Dismiss"
            >
              <AppIcon name="close" size={12} />
            </button>
          </div>
        )}

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
                <th className={`sortable ${sortKey === "engagement_status" ? "sort-active" : ""}`} onClick={() => toggleSort("engagement_status")}>
                  Remark <SortIcon col="engagement_status" />
                </th>
                <th style={{ width: 108 }}></th>
              </tr>
            </thead>
            <tbody>
              {loading ? (
                <tr><td colSpan={7} className="loading-row"><Spinner size={16} /> &nbsp;Loading prospects…</td></tr>
              ) : paged.length === 0 ? (
                <tr>
                  <td colSpan={7}>
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
                        <RemarkPill status={row.engagement_status} />
                      </td>
                      <td>
                        <div className="row-actions">
                          {row.has_outreach_message ? (
                            <button
                              className="btn-icon"
                              onClick={() => void requestSendFromRow(row)}
                              disabled={!row.linkedin_profile_url || generatingId === row.id}
                              title={
                                !row.linkedin_profile_url
                                  ? "No LinkedIn profile URL"
                                  : "Send the generated message"
                              }
                              style={row.linkedin_profile_url ? { color: "var(--amber)" } : undefined}
                            >
                              <AppIcon name="zap" size={13} />
                            </button>
                          ) : (
                            <button
                              className="btn-icon"
                              onClick={() => void handleGenerate(row.id)}
                              disabled={!row.linkedin_profile_url || generatingId === row.id}
                              title={
                                !row.linkedin_profile_url
                                  ? "No LinkedIn profile URL"
                                  : "Generate an outreach message for this prospect"
                              }
                            >
                              {generatingId === row.id ? <Spinner size={13} /> : <AppIcon name="pulse" size={13} />}
                            </button>
                          )}
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
              <div style={{ display: "flex", flexDirection: "column", alignItems: "flex-end", gap: 6, flexShrink: 0 }}>
                <div style={{ display: "flex", gap: 8 }}>
                  {detail.linkedin_profile_url && (
                    <a href={detail.linkedin_profile_url} target="_blank" rel="noreferrer" className="btn-secondary">
                      <AppIcon name="linkedin" size={13} /> LinkedIn
                    </a>
                  )}
                  <button className="btn-secondary" onClick={() => void handleCheckMessagesNow()} disabled={checkingMessages}>
                    {checkingMessages ? <Spinner size={13} /> : <AppIcon name="refresh" size={13} />}
                    Check messages now
                  </button>
                </div>
                {checkResult && (
                  <span style={{ fontSize: "0.74rem", fontWeight: 600, color: checkResult.ok ? "var(--success)" : "var(--danger, #dc2626)" }}>
                    {checkResult.ok ? "✓ " : "⚠ "}{checkResult.text}
                  </span>
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
                    <div style={{ display: "flex", gap: 32, flexWrap: "wrap" }}>
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
                      <div className="kv-item">
                        <span className="kv-label">Remark</span>
                        <RemarkPill status={detail.engagement_status} />
                      </div>
                      {detail.conversation?.lead_stage && (
                        <div className="kv-item">
                          <span className="kv-label">Lead Stage</span>
                          <span className="kv-value">{detail.conversation.lead_stage}</span>
                        </div>
                      )}
                      {detail.conversation && (
                        <div className="kv-item">
                          <span className="kv-label">Messages</span>
                          <span className="kv-value">
                            {detail.conversation.messages_sent ?? 0} sent / {detail.conversation.messages_received ?? 0} received
                          </span>
                        </div>
                      )}
                    </div>
                    {detail.conversation?.handoff_reason && (
                      <div style={{
                        fontSize: "0.8rem", color: "var(--ink-2)",
                        background: "var(--bg-subtle)", padding: "10px 14px",
                        borderRadius: "var(--r-md)", border: "1px solid var(--border)",
                      }}>
                        <strong>Why:</strong> {detail.conversation.handoff_reason}
                      </div>
                    )}
                    <div>
                      <div style={{
                        display: "flex", alignItems: "center", gap: 10, marginBottom: 8,
                      }}>
                        <div className="form-label" style={{ marginBottom: 0 }}>Generated Message</div>
                        {draftDirty && (
                          <span style={{ fontSize: "0.7rem", color: "var(--amber, #b45309)", fontWeight: 600 }}>
                            Unsaved changes
                          </span>
                        )}
                        <div style={{ marginLeft: "auto", display: "flex", gap: 8 }}>
                          <button
                            className="btn-secondary"
                            onClick={() => void handleGenerate(detail.id)}
                            disabled={generatingId === detail.id || !detail.linkedin_profile_url}
                            title={
                              !detail.linkedin_profile_url
                                ? "No LinkedIn profile URL for this prospect"
                                : messageDraft.trim()
                                  ? "Regenerate the AI message (replaces the current text)"
                                  : "Generate an AI outreach message for this prospect"
                            }
                          >
                            {generatingId === detail.id ? <Spinner size={13} /> : <AppIcon name="pulse" size={13} />}
                            {generatingId === detail.id
                              ? "Generating…"
                              : messageDraft.trim() ? "Regenerate" : "Generate"}
                          </button>
                          <button
                            className="btn-secondary"
                            onClick={() => void handleSaveMessage()}
                            disabled={savingMessage || !draftDirty || !messageDraft.trim()}
                            title={draftDirty ? "Save your edits" : "No changes to save"}
                          >
                            {savingMessage ? <Spinner size={13} /> : <AppIcon name="check" size={13} />}
                            {savingMessage ? "Saving…" : "Save"}
                          </button>
                          <button
                            className="btn-primary"
                            onClick={requestSendFromModal}
                            disabled={sending || !messageDraft.trim() || !detail.linkedin_profile_url}
                            title={
                              !detail.linkedin_profile_url
                                ? "No LinkedIn profile URL for this prospect"
                                : !messageDraft.trim()
                                  ? "Write a message first"
                                  : "Send this message on LinkedIn"
                            }
                          >
                            <AppIcon name="zap" size={13} />
                            Send
                          </button>
                        </div>
                      </div>
                      <textarea
                        className="form-input"
                        value={messageDraft}
                        onChange={(e) => setMessageDraft(e.target.value)}
                        rows={8}
                        placeholder="No message yet — click Generate to draft one with AI, or write your own here."
                        style={{
                          width: "100%", resize: "vertical", lineHeight: 1.7,
                          fontSize: "0.88rem", padding: "16px 20px",
                          borderRadius: "var(--r-lg)", color: "var(--ink-1)",
                        }}
                      />
                      <div style={{
                        display: "flex", alignItems: "center", gap: 10, marginTop: 6,
                        fontSize: "0.72rem", color: "var(--ink-3)",
                      }}>
                        <span>{messageDraft.trim().length} characters</span>
                        {sendResult && (
                          <span style={{
                            marginLeft: "auto", fontWeight: 600,
                            color: sendResult.ok ? "var(--success)" : "var(--danger, #dc2626)",
                          }}>
                            {sendResult.text}
                          </span>
                        )}
                      </div>
                    </div>
                  </div>
                )}
              </div>
            )}
          </div>
        )}
      </Modal>

      {/* Send confirmation — shown for both the row button and the modal button */}
      <Modal
        open={!!confirmSend}
        onClose={() => { if (!sending) setConfirmSend(null); }}
        title="Send this message on LinkedIn?"
        size="md"
        footer={
          <>
            <button
              className="btn-secondary"
              onClick={() => setConfirmSend(null)}
              disabled={sending}
            >
              Cancel
            </button>
            <button
              className="btn-primary"
              onClick={() => void handleConfirmedSend()}
              disabled={sending || confirmLoading || !confirmSend?.message.trim()}
            >
              {sending ? <Spinner size={13} /> : <AppIcon name="zap" size={13} />}
              {sending ? "Sending…" : "Send now"}
            </button>
          </>
        }
      >
        {confirmSend && (
          <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
            <div style={{ fontSize: "0.88rem", color: "var(--ink-1)" }}>
              This will send the message below to{" "}
              <strong>{confirmSend.name}</strong> on LinkedIn, right now.
            </div>

            {confirmLoading ? (
              <div style={{ display: "flex", gap: 8, alignItems: "center", color: "var(--ink-3)", padding: 12 }}>
                <Spinner size={14} /> Loading message…
              </div>
            ) : confirmSend.message.trim() ? (
              <div style={{
                background: "var(--bg-subtle)", padding: "14px 18px",
                borderRadius: "var(--r-lg)", lineHeight: 1.7,
                fontSize: "0.85rem", whiteSpace: "pre-wrap",
                border: "1px solid var(--border)", color: "var(--ink-1)",
                maxHeight: 260, overflowY: "auto",
              }}>
                {confirmSend.message}
              </div>
            ) : (
              <div className="error-banner" style={{ borderRadius: "var(--r-md)", padding: "10px 14px", fontSize: "0.82rem" }}>
                No message to send. Run the Intelligence pipeline, or write one in the Outreach tab.
              </div>
            )}

            {confirmSend.dirty && (
              <div style={{ fontSize: "0.76rem", color: "var(--amber, #b45309)", fontWeight: 600 }}>
                Your unsaved edits will be saved and sent.
              </div>
            )}

            <div style={{ fontSize: "0.74rem", color: "var(--ink-3)", lineHeight: 1.5 }}>
              If you aren't connected, LinkedIn may not allow a direct message — in that case a
              connection request is sent instead, carrying this text as the invite note. Replies are
              tracked automatically from then on.
            </div>
          </div>
        )}
      </Modal>
    </div>
  );
}
