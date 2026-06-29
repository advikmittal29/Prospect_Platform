// import { FormEvent, useEffect, useMemo, useState } from "react";
// import { useOutletContext } from "react-router-dom";
// // import { getCandidateProfileDetail, getCandidateProfiles } from "../api/client";
// import type { CandidateProfileDetail, CandidateProfileRow } from "../api/types";
// import type { AgentScopeContextValue } from "../agentScope";
// import { Modal } from "../components/Modal";
// import { Pagination } from "../components/Pagination";
// import { SectionCard } from "../components/SectionCard";

// const PAGE_SIZE = 10;

// function parseJ(v: unknown): unknown {
//   if (typeof v !== "string") return v;
//   try {
//     return JSON.parse(v);
//   } catch {
//     return v;
//   }
// }

// function safe(v: unknown): string {
//   if (v == null || v === "") return "Not available";
//   if (typeof v === "string" || typeof v === "number" || typeof v === "boolean") return String(v);
//   if (Array.isArray(v)) return v.slice(0, 4).map(safe).join(" | ");
//   const r = v as Record<string, unknown>;
//   return Object.entries(r)
//     .slice(0, 3)
//     .map(([k, x]) => `${k}: ${safe(x)}`)
//     .join(" | ");
// }

// function listify(v: unknown): string[] {
//   const parsed = parseJ(v);
//   if (!Array.isArray(parsed)) return [];
//   return parsed.map((x) => safe(x)).filter((x) => x && x !== "Not available");
// }

// function objectEntries(v: unknown): Array<{ key: string; value: string }> {
//   const parsed = parseJ(v);
//   if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return [];
//   return Object.entries(parsed as Record<string, unknown>).map(([k, value]) => ({
//     key: k,
//     value: safe(value),
//   }));
// }

// export function CandidatesPage() {
//   const { activeAgentId, agentScopeVersion } = useOutletContext<AgentScopeContextValue>();
//   const [rows, setRows] = useState<CandidateProfileRow[]>([]);
//   const [loading, setLoading] = useState(false);
//   const [error, setError] = useState("");
//   const [search, setSearch] = useState("");
//   const [status, setStatus] = useState("");
//   const [jobId, setJobId] = useState("");
//   const [page, setPage] = useState(1);

//   const [selectedId, setSelectedId] = useState<number | null>(null);
//   const [detail, setDetail] = useState<CandidateProfileDetail | null>(null);

//   const load = async () => {
//     setLoading(true);
//     setError("");
//     try {
//       const result = await getCandidateProfiles({
//         search: search || undefined,
//         status: status || undefined,
//         job_id: jobId ? Number(jobId) : undefined,
//         page_size: 500,
//         agent_id: activeAgentId,
//       });
//       setRows(result.items);
//       setPage(1);
//     } catch (err) {
//       setError(err instanceof Error ? err.message : "Failed to load candidate profiles");
//     } finally {
//       setLoading(false);
//     }
//   };

//   useEffect(() => {
//     void load();
//     // eslint-disable-next-line react-hooks/exhaustive-deps
//   }, [agentScopeVersion]);

//   const onFilter = (e: FormEvent) => {
//     e.preventDefault();
//     void load();
//   };

//   const openDetail = async (id: number) => {
//     setSelectedId(id);
//     try {
//       const d = await getCandidateProfileDetail(id, activeAgentId);
//       setDetail(d);
//     } catch (err) {
//       setError(err instanceof Error ? err.message : "Failed to load candidate detail");
//     }
//   };

//   const pagedRows = useMemo(
//     () => rows.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE),
//     [rows, page]
//   );

//   const skills = useMemo(() => listify(detail?.skills_json), [detail]);
//   const topEvidence = useMemo(() => listify(detail?.top_evidence_json), [detail]);
//   const negativeEvidence = useMemo(() => listify(detail?.negative_evidence_json), [detail]);
//   const missingCritical = useMemo(
//     () => listify(detail?.missing_critical_requirements_json),
//     [detail]
//   );
//   const jdScores = useMemo(() => objectEntries(detail?.jd_dimension_scores_json), [detail]);

//   return (
//     <div className="page-stack">
//       <div className="page-header fade-in">
//         <div className="page-header-left">
//           <h1>Candidate Profiles</h1>
//           <p className="page-sub">
//             LinkedIn candidates fetched and stored in DB.
//             {activeAgentId ? ` (Agent #${activeAgentId})` : ""}
//           </p>
//         </div>
//       </div>

//       <SectionCard title="Search & Filter" subtitle="Find candidate records by status, job, or keywords">
//         <form className="filter-grid" onSubmit={onFilter}>
//           <label>
//             Search
//             <input
//               value={search}
//               onChange={(e) => setSearch(e.target.value)}
//               placeholder="name / headline / current title"
//             />
//           </label>
//           <label>
//             Status
//             <select value={status} onChange={(e) => setStatus(e.target.value)}>
//               <option value="">All</option>
//               <option value="queued">Queued</option>
//               <option value="completed">Completed</option>
//               <option value="profile_failed">Profile Failed</option>
//               <option value="scoring_failed">Scoring Failed</option>
//               <option value="failed">Failed</option>
//             </select>
//           </label>
//           <label>
//             Job ID
//             <input value={jobId} onChange={(e) => setJobId(e.target.value)} placeholder="numeric ID" />
//           </label>
//           <div className="form-actions">
//             <button className="btn-primary" type="submit" disabled={loading}>
//               {loading ? "Loading" : "Apply Filters"}
//             </button>
//           </div>
//         </form>
//       </SectionCard>

//       {error ? (
//         <div className="error-banner">
//           <svg
//             width="15"
//             height="15"
//             viewBox="0 0 24 24"
//             fill="none"
//             stroke="currentColor"
//             strokeWidth="2"
//             strokeLinecap="round"
//             style={{ flexShrink: 0 }}
//           >
//             <circle cx="12" cy="12" r="10" />
//             <path d="M12 8v4M12 16h.01" />
//           </svg>
//           {error}
//         </div>
//       ) : null}

//       <SectionCard title="Candidate Results" subtitle={`${rows.length} candidates found`} noPad>
//         <div className="table-wrap fixed-height" style={{ border: "none", borderRadius: 0 }}>
//           <table>
//             <thead>
//               <tr>
//                 <th>ID</th>
//                 <th>Full Name</th>
//                 <th>Headline</th>
//                 <th>Job</th>
//                 <th>Status</th>
//                 <th>Seek Score</th>
//                 <th>JD Score</th>
//                 <th>Updated</th>
//                 <th />
//               </tr>
//             </thead>
//             <tbody>
//               {pagedRows.map((row) => (
//                 <tr key={row.id} className={selectedId === row.id ? "row-active" : ""}>
//                   <td className="cell-mono">#{row.id}</td>
//                   <td className="cell-primary">{row.full_name ?? "Not available"}</td>
//                   <td>{row.headline ?? "Not available"}</td>
//                   <td>
//                     {row.job_id}
//                     {row.job_title ? ` | ${row.job_title}` : ""}
//                   </td>
//                   <td>{row.profile_status ?? "Not available"}</td>
//                   <td className="cell-mono">{row.job_seeking_score ?? "-"}</td>
//                   <td className="cell-mono">{row.jd_relevance_score ?? "-"}</td>
//                   <td>{row.updated_at_utc ?? "-"}</td>
//                   <td>
//                     <button className="btn-ghost btn-sm" onClick={() => void openDetail(row.id)}>
//                       View
//                     </button>
//                   </td>
//                 </tr>
//               ))}
//               {pagedRows.length === 0 ? (
//                 <tr>
//                   <td
//                     colSpan={9}
//                     style={{
//                       textAlign: "center",
//                       color: "var(--ink-2)",
//                       padding: "40px",
//                       fontStyle: "italic",
//                     }}
//                   >
//                     No candidate profiles found.
//                   </td>
//                 </tr>
//               ) : null}
//             </tbody>
//           </table>
//         </div>
//         <Pagination page={page} pageSize={PAGE_SIZE} totalItems={rows.length} onPageChange={setPage} />
//       </SectionCard>

//       <Modal
//         open={!!detail}
//         onClose={() => {
//           setDetail(null);
//           setSelectedId(null);
//         }}
//         title={detail?.full_name ?? "Candidate Detail"}
//         subtitle={detail?.headline ?? "Detailed candidate profile intelligence"}
//         size="xl"
//       >
//         {detail ? (
//           <>
//             <div className="info-grid">
//               <article className="info-card">
//                 <h4>Profile Status</h4>
//                 <p>{safe(detail.profile_status)}</p>
//               </article>
//               <article className="info-card">
//                 <h4>Job ID</h4>
//                 <p>{safe(detail.job_id)}</p>
//               </article>
//               <article className="info-card">
//                 <h4>Job Seeking</h4>
//                 <p>
//                   {safe(detail.job_seeking_status)} ({safe(detail.job_seeking_score)})
//                 </p>
//               </article>
//               <article className="info-card">
//                 <h4>JD Relevance</h4>
//                 <p>{safe(detail.jd_relevance_score)}</p>
//               </article>
//               <article className="info-card">
//                 <h4>Confidence</h4>
//                 {/* <p>{safe(detail.confidence_score)}</p> */}
//               </article>
//               <article className="info-card">
//                 <h4>Current Role</h4>
//                 <p>{safe(detail.current_title)}</p>
//               </article>
//             </div>

//             <div className="button-row">
//               {detail.linkedin_profile_url ? (
//                 <a className="btn-primary" href={detail.linkedin_profile_url} target="_blank" rel="noreferrer">
//                   LinkedIn Profile
//                 </a>
//               ) : null}
//               {/* {detail.source_search_url ? (
//                 <a className="btn-ghost" href={detail.source_search_url} target="_blank" rel="noreferrer">
//                   Search Source
//                 </a>
//               ) : null} */}
//             </div>

//             <section>
//               <h3>LLM Summary</h3>
//               <article className="list-card">
//                 <p className="list-body">{safe(detail.llm_summary_text)}</p>
//               </article>
//             </section>

//             <section>
//               <h3>Skills</h3>
//               {skills.length ? (
//                 skills.map((item, idx) => (
//                   <article key={idx} className="list-card">
//                     <p className="list-body">{item}</p>
//                   </article>
//                 ))
//               ) : (
//                 <p className="muted">No skills captured.</p>
//               )}
//             </section>

//             <section>
//               <h3>Top Evidence</h3>
//               {topEvidence.length ? (
//                 topEvidence.map((item, idx) => (
//                   <article key={idx} className="list-card">
//                     <p className="list-body">{item}</p>
//                   </article>
//                 ))
//               ) : (
//                 <p className="muted">No positive evidence captured.</p>
//               )}
//             </section>

//             <section>
//               <h3>Negative Evidence</h3>
//               {negativeEvidence.length ? (
//                 negativeEvidence.map((item, idx) => (
//                   <article key={idx} className="list-card">
//                     <p className="list-body">{item}</p>
//                   </article>
//                 ))
//               ) : (
//                 <p className="muted">No negative evidence captured.</p>
//               )}
//             </section>

//             <section>
//               <h3>Missing Critical Requirements</h3>
//               {missingCritical.length ? (
//                 missingCritical.map((item, idx) => (
//                   <article key={idx} className="list-card">
//                     <p className="list-body">{item}</p>
//                   </article>
//                 ))
//               ) : (
//                 <p className="muted">No missing requirements detected.</p>
//               )}
//             </section>

//             <section>
//               <h3>JD Dimension Scores</h3>
//               {jdScores.length ? (
//                 <div className="kv-grid">
//                   {jdScores.map((item, idx) => (
//                     <article key={idx} className="kv-item">
//                       <p className="kv-label">{item.key}</p>
//                       <p className="kv-value">{item.value}</p>
//                     </article>
//                   ))}
//                 </div>
//               ) : (
//                 <p className="muted">No dimension scores available.</p>
//               )}
//             </section>
//           </>
//         ) : null}
//       </Modal>
//     </div>
//   );
// }
