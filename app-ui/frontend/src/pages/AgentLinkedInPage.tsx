import { useState } from "react";
import { useParams } from "react-router-dom";
import { generateLinkedInMessage, sendLinkedInMessage } from "../api/client";
import { AppIcon, Modal, Spinner } from "../components/index";

const PROFILE_URL_RE = /^https?:\/\/([a-z]{2,3}\.)?linkedin\.com\/in\/[a-zA-Z0-9\-_%]+\/?(\?.*)?$/;

export function AgentLinkedInPage() {
  const { agentId } = useParams<{ agentId: string }>();
  const id = Number(agentId);

  const [profileUrl, setProfileUrl] = useState("");
  const [message, setMessage]       = useState("");
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [sending, setSending]       = useState(false);
  const [result, setResult]         = useState<{ ok: boolean; text: string } | null>(null);

  const [generating, setGenerating]         = useState(false);
  const [generateError, setGenerateError]   = useState("");
  const [generatedForUrl, setGeneratedForUrl] = useState("");
  const [draftMeta, setDraftMeta]           = useState<{ name?: string | null; current_company?: string | null } | null>(null);

  const urlValid = PROFILE_URL_RE.test(profileUrl.trim());
  const canSubmit = urlValid && message.trim().length > 0;

  const runGenerate = async () => {
    const url = profileUrl.trim();
    if (!urlValid || generating) return;
    setGenerating(true);
    setGenerateError("");
    try {
      const r = await generateLinkedInMessage({ profile_url: url, agent_id: id });
      setMessage(r.message);
      setGeneratedForUrl(url);
      setDraftMeta({ name: r.name, current_company: r.current_company });
    } catch (err) {
      setGenerateError(err instanceof Error ? err.message : "Could not generate a message for this profile.");
    } finally {
      setGenerating(false);
    }
  };

  // Auto-draft the first message once the URL is valid and left — mirrors how
  // every other prospect already gets an auto-generated message; the user
  // only needs to review/edit before sending, not write one from scratch.
  const handleUrlBlur = () => {
    if (urlValid && !generating && message.trim() === "" && generatedForUrl !== profileUrl.trim()) {
      void runGenerate();
    }
  };

  const handleSend = async () => {
    setSending(true);
    setResult(null);
    try {
      const r = await sendLinkedInMessage({
        profile_url: profileUrl.trim(),
        message: message.trim(),
        agent_id: id,
      });
      setResult({ ok: true, text: `Message sent — added as prospect #${r.prospect_id} and now tracked for replies.` });
      setProfileUrl("");
      setMessage("");
      setGeneratedForUrl("");
      setDraftMeta(null);
    } catch (err) {
      setResult({ ok: false, text: err instanceof Error ? err.message : "Send failed" });
    } finally {
      setSending(false);
      setConfirmOpen(false);
    }
  };

  return (
    <div className="page-stack fade-in">
      <div className="page-header">
        <div>
          <h1>LinkedIn</h1>
          <p className="page-sub">Send a first message to any LinkedIn profile — connected or not. A successful send auto-registers the person as a tracked prospect.</p>
        </div>
      </div>

      {result && (
        <div className={result.ok ? "" : "error-banner"} style={
          result.ok
            ? { background: "var(--success-soft)", border: "1px solid var(--success-border)", color: "var(--success)", padding: "10px 16px", borderRadius: "var(--r-md)", fontSize: "0.84rem", fontWeight: 600 }
            : undefined
        }>
          {result.ok ? "✓ " : ""}{result.text}
        </div>
      )}

      <div style={{
        display: "flex", flexDirection: "column", gap: 16, maxWidth: 640,
        padding: 20, background: "var(--bg-subtle)", borderRadius: "var(--r-lg)", border: "1px solid var(--border)",
      }}>
        <div className="form-group">
          <label className="form-label">LinkedIn profile URL</label>
          <input
            className="form-input"
            type="text"
            placeholder="https://www.linkedin.com/in/username/"
            value={profileUrl}
            onChange={(e) => setProfileUrl(e.target.value)}
            onBlur={handleUrlBlur}
          />
          {profileUrl.trim().length > 0 && !urlValid && (
            <span style={{ fontSize: "0.74rem", color: "var(--danger, #dc2626)" }}>
              Not a valid LinkedIn profile URL.
            </span>
          )}
        </div>

        <div className="form-group">
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
            <label className="form-label">First message</label>
            <button
              type="button"
              className="btn-icon"
              title={message.trim() ? "Regenerate a fresh draft" : "Generate a first message from this profile"}
              onClick={() => void runGenerate()}
              disabled={!urlValid || generating}
              style={{ display: "flex", alignItems: "center", gap: 6, fontSize: "0.74rem", color: "var(--ink-2)" }}
            >
              {generating ? <Spinner size={12} /> : <AppIcon name="refresh" size={12} />}
              {message.trim() ? "Regenerate" : "Generate"}
            </button>
          </div>
          <textarea
            className="form-input"
            rows={6}
            placeholder={generating ? "Generating a personalized message from this profile…" : "Hi there, I came across your profile and..."}
            value={message}
            onChange={(e) => setMessage(e.target.value)}
            disabled={generating}
          />
          {draftMeta?.name && !generating && (
            <span style={{ fontSize: "0.74rem", color: "var(--text-disabled)" }}>
              Drafted from {draftMeta.name}{draftMeta.current_company ? ` · ${draftMeta.current_company}` : ""} — feel free to edit before sending.
            </span>
          )}
          {generateError && (
            <span style={{ fontSize: "0.74rem", color: "var(--danger, #dc2626)" }}>{generateError}</span>
          )}
        </div>

        <div>
          <button
            className="btn-primary"
            disabled={!canSubmit || sending}
            onClick={() => setConfirmOpen(true)}
          >
            {sending ? <Spinner size={13} /> : <AppIcon name="linkedin" size={13} />}
            Send message
          </button>
        </div>
      </div>

      <Modal open={confirmOpen} onClose={() => setConfirmOpen(false)} title="Confirm send" size="sm">
        <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
          <p style={{ fontSize: "0.88rem", color: "var(--ink-1)" }}>
            Send this message to <strong>{profileUrl.trim()}</strong>?
          </p>
          <pre style={{
            background: "var(--surface)", border: "1px solid var(--border)",
            borderRadius: "var(--r-md)", padding: 12, fontSize: "0.82rem",
            whiteSpace: "pre-wrap", color: "var(--ink-1)",
          }}>
            {message.trim()}
          </pre>
          <div style={{ display: "flex", justifyContent: "flex-end", gap: 8 }}>
            <button className="btn-secondary" onClick={() => setConfirmOpen(false)} disabled={sending}>
              Cancel
            </button>
            <button className="btn-primary" onClick={() => void handleSend()} disabled={sending}>
              {sending ? <Spinner size={13} /> : null}
              Confirm &amp; send
            </button>
          </div>
        </div>
      </Modal>
    </div>
  );
}
