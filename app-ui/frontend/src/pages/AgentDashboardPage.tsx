import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { getAgent, getDashboardSummary, getCompanies, getProspects, getRunDetail, getRuns, triggerRun } from "../api/client";
import type { AgentDefinitionRow, CompanyRow, DashboardSummary, PipelineRun, PipelineType, ProspectRow } from "../api/types";
import { AppIcon, Modal, SectionCard, Spinner } from "../components/index";

/* ─── Mini chart helpers (pure SVG, no deps) ─────────────────────── */
function SparkBar({ values, color = "var(--blue)", height = 40 }: { values: number[]; color?: string; height?: number }) {
  if (!values.length) return null;
  const max = Math.max(...values, 1);
  const W = 180, H = height, gap = 3;
  const bw = (W - gap * (values.length - 1)) / values.length;
  return (
    <svg viewBox={`0 0 ${W} ${H}`} width="100%" height={H} preserveAspectRatio="none">
      {values.map((v, i) => {
        const bh = Math.max(2, (v / max) * H);
        return (
          <rect
            key={i}
            x={i * (bw + gap)}
            y={H - bh}
            width={bw}
            height={bh}
            rx={2}
            fill={color}
            opacity={0.85}
          />
        );
      })}
    </svg>
  );
}

function DonutChart({ filled, total, color = "var(--blue)", size = 80 }: { filled: number; total: number; color?: string; size?: number }) {
  const pct = total > 0 ? filled / total : 0;
  const r = 30, cx = 40, cy = 40, circumference = 2 * Math.PI * r;
  const dash = pct * circumference;
  return (
    <svg width={size} height={size} viewBox="0 0 80 80">
      <circle cx={cx} cy={cy} r={r} fill="none" stroke="var(--bg-subtle)" strokeWidth={10} />
      <circle
        cx={cx} cy={cy} r={r}
        fill="none" stroke={color} strokeWidth={10}
        strokeDasharray={`${dash} ${circumference - dash}`}
        strokeLinecap="round"
        transform={`rotate(-90 ${cx} ${cy})`}
        style={{ transition: "stroke-dasharray 0.6s ease" }}
      />
      <text x={cx} y={cy} textAnchor="middle" dominantBaseline="middle"
        fontSize="13" fontWeight="800" fill="var(--ink-0)" fontFamily="var(--font-sans)">
        {total > 0 ? `${Math.round(pct * 100)}%` : "—"}
      </text>
    </svg>
  );
}

function HorizBar({ label, value, max, color }: { label: string; value: number; max: number; color: string }) {
  const pct = max > 0 ? (value / max) * 100 : 0;
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 10, fontSize: "0.8rem" }}>
      <span style={{ width: 100, color: "var(--ink-2)", flexShrink: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{label}</span>
      <div style={{ flex: 1, height: 6, background: "var(--bg-subtle)", borderRadius: "var(--r-full)", overflow: "hidden" }}>
        <div style={{ width: `${pct}%`, height: "100%", background: color, borderRadius: "var(--r-full)", transition: "width 0.5s ease" }} />
      </div>
      <span style={{ width: 36, textAlign: "right", fontWeight: 700, color: "var(--ink-0)", flexShrink: 0 }}>{value}</span>
    </div>
  );
}

/* ─── Pipeline config ────────────────────────────────────────────── */
const PIPELINES: { key: PipelineType; label: string; desc: string; icon: string; color: string }[] = [
  { key: "ingest",       label: "Run Ingest",      desc: "Pull fresh job signals from boards",    icon: "leads",     color: "var(--blue)"   },
  { key: "research",     label: "Run Research",     desc: "Enrich target companies via LinkedIn",  icon: "companies", color: "var(--violet)" },
  { key: "intelligence", label: "Run Intelligence", desc: "Build dossiers + generate outreach",    icon: "prospects", color: "var(--teal)"   },
];

/* ─── Component ──────────────────────────────────────────────────── */
export function AgentDashboardPage() {
  const { agentId } = useParams<{ agentId: string }>();
  const id = Number(agentId);

  const [agent,   setAgent]   = useState<AgentDefinitionRow | null>(null);
  const [summary, setSummary] = useState<DashboardSummary | null>(null);
  const [runs,    setRuns]    = useState<PipelineRun[]>([]);
  const [topCompanies,  setTopCompanies]  = useState<CompanyRow[]>([]);
  const [hotProspects,  setHotProspects]  = useState<ProspectRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [running, setRunning] = useState<PipelineType | null>(null);
  const [error,   setError]   = useState("");
  const [selectedRun, setSelectedRun] = useState<PipelineRun | null>(null);

  const load = async () => {
    if (!id) return;
    setLoading(true);
    setError("");
    try {
      const [agentData, summaryData, runsData, companiesData, prospectsData] = await Promise.all([
        getAgent(id),
        getDashboardSummary(id),
        getRuns(1, 20, id),
       getCompanies({ page: 1, page_size: 200, agent_id: id }),
       getProspects({ page: 1, page_size: 200, agent_id: id }),
      ]);
      setAgent(agentData);
      setSummary(summaryData);
      setRuns(runsData.items);
      setTopCompanies(companiesData.items.slice(0, 8));
      setHotProspects(prospectsData.items.filter((p) => (p.contact_relevance_score ?? 0) >= 50).slice(0, 8));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load dashboard");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { void load(); }, [agentId]);

  const runPipeline = async (pipeline: PipelineType) => {
    setRunning(pipeline);
    setError("");
    try {
      await triggerRun(pipeline, { agent_id: id });
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to trigger pipeline");
    } finally {
      setRunning(null);
    }
  };

  const openRun = async (run: PipelineRun) => {
    try { setSelectedRun(await getRunDetail(run.id)); }
    catch { setSelectedRun(run); }
  };

  if (loading && !agent) return (
    <div style={{ display: "flex", alignItems: "center", justifyContent: "center", height: "50vh", gap: 12, color: "var(--ink-3)" }}>
      <Spinner size={22} /> <span>Loading agent dashboard…</span>
    </div>
  );

  const m = summary?.metrics ?? {};

  // Derived metrics
  const totalCompanies    = (m.companies_total    as number) ?? topCompanies.length ?? 0;
  const totalProspects    = (m.prospects_total    as number) ?? 0;
  const hotProspectCount  = (m.prospects_hot      as number) ?? hotProspects.length ?? 0;
  const dossierCount      = (m.dossiers_completed as number) ?? 0;
  const outreachReady     = (m.outreach_ready     as number) ?? 0;
  const totalLeads        = (m.jobs_total         as number) ?? 0;
  const researchedLeads   = (m.jobs_researched    as number) ?? 0;
  const completedRuns     = runs.filter((r) => r.status === "completed").length;
  const failedRuns        = runs.filter((r) => r.status === "failed").length;

  // Bucket breakdown for prospects
  const bucketData = [
    { label: "Prime",    value: hotProspects.filter((p) => p.contact_relevance_bucket === "prime").length,    color: "#059669" },
    { label: "Strong",   value: hotProspects.filter((p) => p.contact_relevance_bucket === "strong").length,   color: "#2563eb" },
    { label: "Moderate", value: hotProspects.filter((p) => p.contact_relevance_bucket === "moderate").length, color: "#d97706" },
    { label: "Weak",     value: hotProspects.filter((p) => p.contact_relevance_bucket === "weak").length,     color: "#dc2626" },
  ];
  const maxBucket = Math.max(...bucketData.map((b) => b.value), 1);

  // Research status breakdown for companies
  const researchStatus = [
    { label: "Completed",   value: topCompanies.filter((c) => c.research_status === "completed").length,   color: "#059669" },
    { label: "In Progress", value: topCompanies.filter((c) => c.research_status === "in_progress").length, color: "#2563eb" },
    { label: "Pending",     value: topCompanies.filter((c) => c.research_status === "pending" || !c.research_status).length, color: "#d97706" },
  ];

  // Run activity bars (last 20 runs as bar)
  const runBars = [...runs].reverse().map((r) =>
    r.status === "completed" ? 1 : r.status === "failed" ? 0.3 : 0.6
  );

  const kpis = [
    { label: "Lead Sources",  value: totalLeads,       sub: `${researchedLeads} researched`, icon: "leads",     color: "blue"   },
    { label: "Companies",     value: totalCompanies,   sub: `${researchStatus[0].value} fully mapped`,  icon: "companies", color: "violet" },
    { label: "Prospects",     value: totalProspects,   sub: `${hotProspectCount} high-intent`,          icon: "prospects", color: "teal"   },
    { label: "Hot Prospects", value: hotProspectCount, sub: "prime + strong buckets",                   icon: "zap",       color: "emerald"},
    { label: "Dossiers Built",value: dossierCount,     sub: `of ${totalProspects} prospects`,            icon: "eye",       color: "amber"  },
    { label: "Outreach Ready",value: outreachReady,    sub: "messages generated",                        icon: "linkedin",  color: "blue"   },
  ];

  return (
    <div className="page-stack fade-in">
      {/* Header */}
      <div className="page-header">
        <div>
          <h1>{agent?.name ?? "Agent Dashboard"}</h1>
          <p className="page-sub">{agent?.persona_title ?? agent?.description ?? "Staffing intelligence & outreach pipeline."}</p>
        </div>
        <div className="page-header-actions">
          <span className={`status-pill ${agent?.status ?? "active"}`}>{agent?.status ?? "active"}</span>
          <button className="btn-secondary" onClick={load} disabled={loading}>
            {loading ? <Spinner size={13} /> : <AppIcon name="refresh" size={13} />} Refresh
          </button>
        </div>
      </div>

      {error && <div className="error-banner">{error}</div>}

      {/* ── KPI Cards ── */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))", gap: 14 }}>
        {kpis.map((k) => (
          <div key={k.label} className="kpi-card" style={{ padding: "16px" }}>
            <div className={`kpi-icon ${k.color}`} style={{ width: 40, height: 40, borderRadius: 11 }}>
              <AppIcon name={k.icon as any} size={18} />
            </div>
            <div className="kpi-body">
              <span className="kpi-label">{k.label}</span>
              <span className="kpi-value" style={{ fontSize: "1.55rem" }}>{loading ? "—" : k.value.toLocaleString()}</span>
              <span className="kpi-trend">{k.sub}</span>
            </div>
          </div>
        ))}
      </div>

      {/* ── Pipeline Controls ── */}
      <SectionCard title="Pipeline Controls" subtitle="Trigger workflows for this agent">
        <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 14 }}>
          {PIPELINES.map((p) => (
            <div key={p.key} style={{
              border: "1px solid var(--border)", borderRadius: "var(--r-lg)", padding: "16px",
              display: "flex", flexDirection: "column", gap: 12,
              background: "var(--bg-subtle)",
            }}>
              <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                <div style={{
                  width: 38, height: 38, borderRadius: 10,
                  background: running === p.key ? p.color : "var(--surface)",
                  color: running === p.key ? "white" : p.color,
                  border: `2px solid ${p.color}20`,
                  display: "flex", alignItems: "center", justifyContent: "center",
                  transition: "all 0.25s",
                }}>
                  <AppIcon name={p.icon as any} size={16} />
                </div>
                <div>
                  <div style={{ fontWeight: 700, fontSize: "0.88rem", color: "var(--ink-0)" }}>{p.label}</div>
                  <div style={{ fontSize: "0.72rem", color: "var(--ink-3)", marginTop: 2 }}>{p.desc}</div>
                </div>
              </div>
              <div style={{ fontSize: "0.72rem", color: "var(--ink-3)" }}>
                {runs.filter((r) => r.pipeline === p.key).length} runs ·{" "}
                {runs.find((r) => r.pipeline === p.key)
                  ? `Last: ${runs.find((r) => r.pipeline === p.key)!.status}`
                  : "Never run"}
              </div>
              <button
                className="btn-primary"
                style={{ width: "100%", justifyContent: "center", background: p.color }}
                disabled={running !== null}
                onClick={() => void runPipeline(p.key)}
              >
                {running === p.key
                  ? <><Spinner size={13} /> Running…</>
                  : <><AppIcon name="play" size={12} /> Start</>}
              </button>
            </div>
          ))}
        </div>
      </SectionCard>

      {/* ── Charts row ── */}
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 20 }}>

        {/* Funnel: Lead → Company → Prospect → Outreach */}
        <div className="section-card" style={{ overflow: "hidden" }}>
          <div className="section-card-header">
            <div className="section-card-title">Recruitment Funnel</div>
            <div className="section-card-sub">End-to-end pipeline conversion</div>
          </div>
          <div className="section-card-body">
            <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
              {[
                { label: "Lead Sources",   val: totalLeads,       color: "#2563eb", pct: 100 },
                { label: "Companies",      val: totalCompanies,   color: "#7c3aed", pct: totalLeads  > 0 ? (totalCompanies / totalLeads)  * 100 : 0 },
                { label: "Prospects",      val: totalProspects,   color: "#0d9488", pct: totalLeads  > 0 ? (totalProspects / totalLeads)  * 100 : 0 },
                { label: "Hot Prospects",  val: hotProspectCount, color: "#059669", pct: totalLeads  > 0 ? (hotProspectCount / totalLeads) * 100 : 0 },
                { label: "Outreach Ready", val: outreachReady,    color: "#d97706", pct: totalLeads  > 0 ? (outreachReady / totalLeads)   * 100 : 0 },
              ].map((row) => (
                <div key={row.label}>
                  <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 4, fontSize: "0.78rem" }}>
                    <span style={{ color: "var(--ink-2)", fontWeight: 600 }}>{row.label}</span>
                    <span style={{ fontWeight: 800, color: "var(--ink-0)" }}>{row.val.toLocaleString()}</span>
                  </div>
                  <div style={{ height: 8, background: "var(--bg-subtle)", borderRadius: "var(--r-full)", overflow: "hidden" }}>
                    <div style={{
                      width: `${Math.min(row.pct, 100)}%`, height: "100%",
                      background: row.color, borderRadius: "var(--r-full)",
                      transition: "width 0.6s ease",
                    }} />
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>

        {/* Prospect buckets */}
        <div className="section-card" style={{ overflow: "hidden" }}>
          <div className="section-card-header">
            <div className="section-card-title">Prospect Quality</div>
            <div className="section-card-sub">Relevance bucket breakdown</div>
          </div>
          <div className="section-card-body">
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 20 }}>
              <DonutChart
                filled={hotProspectCount}
                total={totalProspects}
                color="var(--success)"
                size={90}
              />
              <div style={{ textAlign: "right" }}>
                <div style={{ fontSize: "1.8rem", fontWeight: 800, color: "var(--ink-0)", lineHeight: 1 }}>
                  {hotProspectCount}
                </div>
                <div style={{ fontSize: "0.72rem", color: "var(--ink-3)", marginTop: 3 }}>Hot prospects</div>
                <div style={{ fontSize: "0.78rem", color: "var(--ink-2)", marginTop: 4 }}>of {totalProspects} total</div>
              </div>
            </div>
            <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
              {bucketData.map((b) => (
                <HorizBar key={b.label} label={b.label} value={b.value} max={maxBucket} color={b.color} />
              ))}
            </div>
          </div>
        </div>

        {/* Dossier & Outreach progress */}
        <div className="section-card" style={{ overflow: "hidden" }}>
          <div className="section-card-header">
            <div className="section-card-title">Intelligence Progress</div>
            <div className="section-card-sub">Dossier & outreach completion</div>
          </div>
          <div className="section-card-body">
            <div style={{ display: "flex", justifyContent: "space-around", marginBottom: 20, gap: 16 }}>
              <div style={{ textAlign: "center" }}>
                <DonutChart filled={dossierCount} total={totalProspects} color="var(--violet)" size={72} />
                <div style={{ fontSize: "0.72rem", color: "var(--ink-3)", marginTop: 6, fontWeight: 600 }}>Dossiers</div>
              </div>
              <div style={{ textAlign: "center" }}>
                <DonutChart filled={outreachReady} total={totalProspects} color="var(--teal)" size={72} />
                <div style={{ fontSize: "0.72rem", color: "var(--ink-3)", marginTop: 6, fontWeight: 600 }}>Outreach</div>
              </div>
            </div>
            <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
              {[
                { label: "Dossiers built",    value: dossierCount,  total: totalProspects, color: "var(--violet)" },
                { label: "Outreach generated",value: outreachReady, total: totalProspects, color: "var(--teal)"   },
              ].map((row) => (
                <div key={row.label} style={{ display: "flex", alignItems: "center", gap: 10, fontSize: "0.78rem" }}>
                  <span style={{ flex: 1, color: "var(--ink-2)", fontWeight: 600 }}>{row.label}</span>
                  <span style={{ fontWeight: 800, color: "var(--ink-0)" }}>{row.value}</span>
                  <span style={{ color: "var(--ink-3)" }}>/ {row.total}</span>
                </div>
              ))}
              <div style={{ borderTop: "1px solid var(--border)", paddingTop: 10, marginTop: 4 }}>
                <div style={{ display: "flex", justifyContent: "space-between", fontSize: "0.78rem" }}>
                  <span style={{ color: "var(--ink-2)" }}>Pipeline runs</span>
                  <span style={{ fontWeight: 700 }}>{runs.length} total</span>
                </div>
                <div style={{ display: "flex", justifyContent: "space-between", fontSize: "0.78rem", marginTop: 4 }}>
                  <span style={{ color: "var(--success)", fontWeight: 600 }}>✓ Completed</span>
                  <span style={{ fontWeight: 700 }}>{completedRuns}</span>
                </div>
                <div style={{ display: "flex", justifyContent: "space-between", fontSize: "0.78rem", marginTop: 4 }}>
                  <span style={{ color: "var(--danger)", fontWeight: 600 }}>✗ Failed</span>
                  <span style={{ fontWeight: 700 }}>{failedRuns}</span>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>

      {/* ── Company research + run activity ── */}
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 20 }}>

        {/* Company research status */}
        <div className="section-card" style={{ overflow: "hidden" }}>
          <div className="section-card-header">
            <div className="section-card-title">Company Research Status</div>
            <div className="section-card-sub">Research progress across target firms</div>
          </div>
          <div className="section-card-body" style={{ padding: "0" }}>
            <div style={{ display: "flex", gap: 20, padding: "16px 20px", borderBottom: "1px solid var(--border)" }}>
              {researchStatus.map((r) => (
                <div key={r.label} style={{ display: "flex", alignItems: "center", gap: 8, fontSize: "0.8rem" }}>
                  <div style={{ width: 10, height: 10, borderRadius: 3, background: r.color, flexShrink: 0 }} />
                  <span style={{ color: "var(--ink-2)" }}>{r.label}</span>
                  <span style={{ fontWeight: 800, color: "var(--ink-0)" }}>{r.value}</span>
                </div>
              ))}
            </div>
            {topCompanies.length === 0 ? (
              <div className="empty-state" style={{ padding: "32px" }}>
                <AppIcon name="companies" size={28} />
                <p>No companies yet. Run the Research pipeline to start mapping target firms.</p>
              </div>
            ) : (
              <div>
                {topCompanies.map((c, i) => (
                  <div key={c.id} style={{
                    display: "flex", alignItems: "center", gap: 12,
                    padding: "10px 20px",
                    borderBottom: i < topCompanies.length - 1 ? "1px solid var(--border)" : "none",
                    transition: "background 150ms",
                  }}
                    onMouseEnter={(e) => (e.currentTarget.style.background = "var(--bg-subtle)")}
                    onMouseLeave={(e) => (e.currentTarget.style.background = "")}
                  >
                    <div style={{
                      width: 32, height: 32, borderRadius: 8, flexShrink: 0,
                      background: c.research_status === "completed" ? "var(--success-soft)" : "var(--bg-subtle)",
                      display: "flex", alignItems: "center", justifyContent: "center",
                      fontSize: "0.78rem", fontWeight: 800,
                      color: c.research_status === "completed" ? "var(--success)" : "var(--ink-3)",
                    }}>
                      {c.company_name?.[0]?.toUpperCase() ?? "?"}
                    </div>
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div style={{ fontWeight: 650, fontSize: "0.83rem", color: "var(--ink-0)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                        {c.company_name}
                      </div>
                      <div style={{ fontSize: "0.72rem", color: "var(--ink-3)" }}>
                        {c.industry ?? "—"} · {c.employee_range ?? "—"}
                      </div>
                    </div>
                    <div style={{ textAlign: "right", flexShrink: 0 }}>
                      <span className={`status-pill ${c.research_status ?? "pending"}`} style={{ fontSize: "0.62rem" }}>
                        {c.research_status ?? "pending"}
                      </span>
                      <div style={{ fontSize: "0.7rem", color: "var(--ink-3)", marginTop: 3 }}>
                        {c.prospect_count ?? 0} prospects
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
          <div style={{ padding: "10px 20px", borderTop: "1px solid var(--border)", background: "var(--bg-subtle)" }}>
            <Link to={`/agent/${id}/companies`} style={{ fontSize: "0.8rem", color: "var(--blue)", fontWeight: 650 }}>
              View all {totalCompanies} companies →
            </Link>
          </div>
        </div>

        {/* Hot prospects list */}
        <div className="section-card" style={{ overflow: "hidden" }}>
          <div className="section-card-header">
            <div className="section-card-title">Top Prospects</div>
            <div className="section-card-sub">Highest-relevance identified contacts</div>
          </div>
          <div className="section-card-body" style={{ padding: 0 }}>
            {hotProspects.length === 0 ? (
              <div className="empty-state" style={{ padding: "32px" }}>
                <AppIcon name="prospects" size={28} />
                <p>No high-intent prospects yet. Run the Intelligence pipeline to score contacts.</p>
              </div>
            ) : (
              hotProspects.map((p, i) => {
                const score = p.contact_relevance_score ?? 0;
                const fillClass = score >= 70 ? "high" : score >= 40 ? "mid" : "low";
                return (
                  <div key={p.id} style={{
                    display: "flex", alignItems: "center", gap: 12, padding: "10px 20px",
                    borderBottom: i < hotProspects.length - 1 ? "1px solid var(--border)" : "none",
                  }}
                    onMouseEnter={(e) => (e.currentTarget.style.background = "var(--bg-subtle)")}
                    onMouseLeave={(e) => (e.currentTarget.style.background = "")}
                  >
                    <div style={{
                      width: 32, height: 32, borderRadius: "50%", flexShrink: 0,
                      background: "var(--blue)", color: "white",
                      display: "flex", alignItems: "center", justifyContent: "center",
                      fontSize: "0.75rem", fontWeight: 700,
                    }}>
                      {p.name?.[0]?.toUpperCase() ?? "?"}
                    </div>
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div style={{ fontWeight: 650, fontSize: "0.83rem", color: "var(--ink-0)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                        {p.name}
                      </div>
                      <div style={{ fontSize: "0.72rem", color: "var(--ink-3)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                        {p.current_title ?? p.headline ?? "—"} · {p.company_name ?? "—"}
                      </div>
                    </div>
                    <div style={{ textAlign: "right", flexShrink: 0, minWidth: 80 }}>
                      <div className="score-bar" style={{ justifyContent: "flex-end" }}>
                        <div className="score-track" style={{ width: 60 }}>
                          <div className={`score-fill ${fillClass}`} style={{ width: `${score}%` }} />
                        </div>
                        <span className="score-num" style={{ fontSize: "0.7rem" }}>{score}%</span>
                      </div>
                      <div style={{ marginTop: 3 }}>
                        <span className={`status-pill ${p.outreach_dispatch_status ?? "pending"}`} style={{ fontSize: "0.58rem" }}>
                          {p.outreach_dispatch_status ?? "unsent"}
                        </span>
                      </div>
                    </div>
                  </div>
                );
              })
            )}
          </div>
          <div style={{ padding: "10px 20px", borderTop: "1px solid var(--border)", background: "var(--bg-subtle)" }}>
            <Link to={`/agent/${id}/prospects`} style={{ fontSize: "0.8rem", color: "var(--blue)", fontWeight: 650 }}>
              View all {totalProspects} prospects →
            </Link>
          </div>
        </div>
      </div>

      {/* ── Pipeline run history ── */}
      <SectionCard
        title="Pipeline Run History"
        subtitle="Recent executions for this agent"
        noPad
        headerAction={
          runs.length > 0 && (
            <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <div style={{ width: 160 }}>
                <SparkBar values={runBars} color="var(--blue)" height={28} />
              </div>
              <span style={{ fontSize: "0.72rem", color: "var(--ink-3)" }}>{runs.length} runs</span>
            </div>
          )
        }
      >
        <div className="activity-list">
          {loading && runs.length === 0 ? (
            <div className="empty-state" style={{ padding: "40px" }}><Spinner size={20} /><p>Loading run history…</p></div>
          ) : runs.length === 0 ? (
            <div className="empty-state" style={{ padding: "48px" }}>
              <AppIcon name="pulse" size={32} />
              <p>No pipeline runs yet. Trigger a workflow from the controls above.</p>
            </div>
          ) : (
            runs.map((run) => (
              <div key={run.id} className="activity-item" onClick={() => void openRun(run)}>
                <div className={`activity-bar ${run.status}`} />
                <div className="activity-body">
                  <div className="activity-top">
                    <span className="activity-name">{run.pipeline}</span>
                    <span className="activity-time">
                      {run.started_at_utc
                        ? new Date(run.started_at_utc).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })
                        : "—"}
                    </span>
                  </div>
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginTop: 2 }}>
                    <span className="activity-msg">{run.message || `Pipeline ${run.status}`}</span>
                    <span className={`status-pill ${run.status}`}>{run.status}</span>
                  </div>
                </div>
              </div>
            ))
          )}
        </div>
      </SectionCard>

      {/* Run detail modal */}
      <Modal open={!!selectedRun} onClose={() => setSelectedRun(null)} title={`Run #${selectedRun?.id} — ${selectedRun?.pipeline ?? ""}`} size="lg">
        {selectedRun && (
          <div style={{ display: "flex", flexDirection: "column", gap: 20 }}>
            <div className="kv-grid">
              <div className="kv-item"><span className="kv-label">Status</span><span className={`status-pill ${selectedRun.status}`}>{selectedRun.status}</span></div>
              <div className="kv-item"><span className="kv-label">Pipeline</span><span className="kv-value">{selectedRun.pipeline}</span></div>
              <div className="kv-item"><span className="kv-label">Started</span><span className="kv-value">{selectedRun.started_at_utc ?? "—"}</span></div>
              <div className="kv-item"><span className="kv-label">Ended</span><span className="kv-value">{selectedRun.ended_at_utc ?? "Still running…"}</span></div>
            </div>
            <div>
              <div className="form-label" style={{ marginBottom: 8 }}>Execution Log</div>
              <pre style={{
                background: "var(--ink-0)", color: "#10b981",
                padding: "16px", borderRadius: "var(--r-md)",
                fontFamily: "var(--font-mono)", fontSize: "0.75rem",
                maxHeight: 360, overflow: "auto", lineHeight: 1.65,
              }}>
                {selectedRun.log_text || selectedRun.message || "No log output captured."}
              </pre>
            </div>
          </div>
        )}
      </Modal>
    </div>
  );
}
