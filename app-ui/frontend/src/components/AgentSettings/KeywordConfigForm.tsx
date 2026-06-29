import { useEffect, useState } from "react";
import { getAgentKeywords, upsertAgentKeyword, deleteAgentKeyword } from "../../api/client";

interface KeywordConfigFormProps {
  agentId: number;
  onUnsavedChange: (hasChanges: boolean) => void;
}

export function KeywordConfigForm({ agentId, onUnsavedChange }: KeywordConfigFormProps) {
  const [keywords, setKeywords] = useState<any[]>([]);
  const [hasChanges, setHasChanges] = useState(false);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    let mounted = true;
    const load = async () => {
      try {
        const res = await getAgentKeywords(agentId);
        if (!mounted) return;
        setKeywords(res.items || []);
      } catch (err) {
        if (!mounted) return;
        setError("Failed to load keywords");
      } finally {
        if (mounted) setLoading(false);
      }
    };
    load();
    return () => { mounted = false; };
  }, [agentId]);

  const handleAddKeyword = () => {
    const newId = Math.min(...keywords.map((k) => k.id), 0) - 1; // Negative ID for unsaved items
    setKeywords([
      ...keywords,
      { id: newId, keyword_type: "title_include", keyword: "", weight: 1.0, active: true },
    ]);
    setHasChanges(true);
    onUnsavedChange(true);
  };

  const handleRemoveKeyword = async (id: number) => {
    if (id > 0) {
      try {
        await deleteAgentKeyword(agentId, id);
      } catch (err) {
        setError("Failed to delete keyword");
        return;
      }
    }
    setKeywords(keywords.filter((k) => k.id !== id));
    setHasChanges(true);
    onUnsavedChange(true);
  };

  const handleKeywordChange = (
    id: number,
    field: string,
    value: any
  ) => {
    setKeywords(
      keywords.map((k) => (k.id === id ? { ...k, [field]: value } : k))
    );
    setHasChanges(true);
    onUnsavedChange(true);
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setSaving(true);
    setError("");
    try {
      for (const k of keywords) {
        await upsertAgentKeyword(agentId, {
          keyword_type: k.keyword_type,
          keyword: k.keyword,
          weight: k.weight,
          active: k.active
        });
      }
      // Reload from server to get real IDs
      const res = await getAgentKeywords(agentId);
      setKeywords(res.items || []);
      setHasChanges(false);
      onUnsavedChange(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to save keywords");
    } finally {
      setSaving(false);
    }
  };

  if (loading) return <div>Loading keywords...</div>;

  return (
    <form onSubmit={handleSubmit} className="keyword-config-form">
      {error && <div className="error-banner">{error}</div>}
      <div className="keyword-table">
        <div className="table-header">
          <div className="col-type">Type</div>
          <div className="col-keyword">Keyword</div>
          <div className="col-weight">Weight</div>
          <div className="col-active">Active</div>
          <div className="col-actions">Actions</div>
        </div>

        {keywords.map((keyword) => (
          <div key={keyword.id} className="table-row">
            <select
              value={keyword.keyword_type}
              onChange={(e) =>
                handleKeywordChange(keyword.id, "keyword_type", e.target.value)
              }
              className="cell-input"
            >
              <option value="title_include">Title Include</option>
              <option value="title_exclude">Title Exclude</option>
              <option value="company_size">Company Size</option>
              <option value="industry">Industry</option>
              <option value="tech_stack">Tech Stack</option>
            </select>
            <input
              type="text"
              value={keyword.keyword}
              onChange={(e) =>
                handleKeywordChange(keyword.id, "keyword", e.target.value)
              }
              placeholder="e.g., CTO"
              className="cell-input"
            />
            <input
              type="number"
              step="0.1"
              min="0.1"
              max="3"
              value={keyword.weight}
              onChange={(e) =>
                handleKeywordChange(keyword.id, "weight", parseFloat(e.target.value))
              }
              className="cell-input cell-weight"
            />
            <input
              type="checkbox"
              checked={keyword.active}
              onChange={(e) =>
                handleKeywordChange(keyword.id, "active", e.target.checked)
              }
            />
            <button
              type="button"
              onClick={() => handleRemoveKeyword(keyword.id)}
              className="btn-remove"
            >
              Remove
            </button>
          </div>
        ))}
      </div>

      <button
        type="button"
        onClick={handleAddKeyword}
        className="btn-add-keyword"
      >
        + Add Keyword
      </button>

      <div className="form-actions">
        <button type="button" className="btn-secondary">
          Cancel
        </button>
        <button type="submit" className="btn-primary" disabled={!hasChanges || saving}>
          {saving ? "Saving..." : "Save Keywords"}
        </button>
      </div>

      <style>{`
        .keyword-config-form {
          display: flex;
          flex-direction: column;
          gap: 24px;
        }

        .keyword-table {
          border: 1px solid var(--border);
          border-radius: 12px;
          overflow: hidden;
          background: var(--bg-card);
        }

        .table-header {
          display: grid;
          grid-template-columns: 160px 1fr 100px 80px 100px;
          gap: 0;
          background: var(--bg-subtle);
          border-bottom: 1px solid var(--border);
        }

        .table-header > * {
          padding: 14px 16px;
          font-weight: 700;
          font-size: 0.75rem;
          color: var(--ink-2);
          text-transform: uppercase;
          letter-spacing: 0.05em;
        }

        .table-row {
          display: grid;
          grid-template-columns: 160px 1fr 100px 80px 100px;
          gap: 0;
          border-bottom: 1px solid var(--border);
          transition: background 0.2s;
        }
        
        .table-row:hover { background: rgba(0,0,0,0.02); }
        .table-row:last-child { border-bottom: none; }

        .table-row > * {
          padding: 10px 16px;
          display: flex;
          align-items: center;
        }

        .cell-input {
          width: 100%;
          padding: 8px 12px;
          border: 1px solid var(--border);
          border-radius: 8px;
          font-size: 0.9rem;
          font-family: inherit;
          background: var(--bg-page);
          color: var(--ink-base);
          transition: all 0.2s;
        }
        
        .cell-input:focus {
           outline: none;
           border-color: var(--blue);
           background: white;
        }

        .cell-weight {
          width: 80px;
          font-family: var(--font-mono);
          font-weight: 700;
        }

        .btn-remove {
          padding: 6px 12px;
          background: var(--danger-soft);
          color: var(--danger);
          border: 1px solid var(--danger-soft);
          border-radius: 8px;
          font-size: 0.8rem;
          font-weight: 700;
          cursor: pointer;
          transition: all 0.2s;
        }
        
        .btn-remove:hover { border-color: var(--danger); }

        .btn-add-keyword {
          padding: 12px 20px;
          background: var(--bg-subtle);
          border: 1px dashed var(--border);
          border-radius: 12px;
          color: var(--blue);
          font-size: 0.9rem;
          font-weight: 700;
          cursor: pointer;
          display: flex;
          align-items: center;
          justify-content: center;
          gap: 8px;
          transition: all 0.2s;
        }

        .btn-add-keyword:hover {
          background: var(--blue-soft);
          border-style: solid;
          border-color: var(--blue);
        }

        .form-actions {
          display: flex;
          gap: 12px;
          justify-content: flex-end;
          margin-top: 24px;
          padding-top: 24px;
          border-top: 1px solid var(--border);
        }

        .btn-primary,
        .btn-secondary {
          padding: 12px 24px;
          border-radius: 12px;
          font-size: 0.9rem;
          font-weight: 700;
          cursor: pointer;
          border: 1px solid transparent;
          transition: all 0.2s;
        }

        .btn-primary {
          background: var(--blue);
          color: white;
          box-shadow: 0 4px 12px var(--blue-soft);
        }

        .btn-primary:hover:not(:disabled) {
          transform: translateY(-1px);
        }

        .btn-primary:disabled {
          opacity: 0.5;
          filter: grayscale(1);
        }

        .btn-secondary {
          background: var(--bg-subtle);
          border-color: var(--border);
          color: var(--ink-1);
        }
      `}</style>
    </form>
  );
}
