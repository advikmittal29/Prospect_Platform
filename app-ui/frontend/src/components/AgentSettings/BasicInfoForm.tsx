import { useEffect, useState } from "react";
import { getAgent, updateAgent } from "../../api/client";

interface BasicInfoFormProps {
  agentId: number;
  onUnsavedChange: (hasChanges: boolean) => void;
}

export function BasicInfoForm({ agentId, onUnsavedChange }: BasicInfoFormProps) {
  const [formData, setFormData] = useState({
    name: "",
    description: "",
    agent_type: "custom",
    status: "active" as "active" | "paused" | "archived",
  });
  const [hasChanges, setHasChanges] = useState(false);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    let mounted = true;
    const load = async () => {
      try {
        const agent = await getAgent(agentId);
        if (!mounted) return;
        setFormData({
          name: agent.name || "",
          description: agent.description || "",
          agent_type: agent.agent_type || "custom",
          status: (agent.status as any) || "active",
        });
      } catch (err) {
        if (!mounted) return;
        setError("Failed to load agent");
      } finally {
        if (mounted) setLoading(false);
      }
    };
    load();
    return () => { mounted = false; };
  }, [agentId]);


  const handleChange = (
    e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement>
  ) => {
    const { name, value } = e.target;
    setFormData((prev) => ({ ...prev, [name]: value }));
    setHasChanges(true);
    onUnsavedChange(true);
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setSaving(true);
    setError("");
    try {
      await updateAgent(agentId, formData);
      setHasChanges(false);
      onUnsavedChange(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to update");
    } finally {
      setSaving(false);
    }
  };

  if (loading) return <div>Loading...</div>;

  return (
    <form onSubmit={handleSubmit} className="basic-info-form">
      {error && <div className="error-banner">{error}</div>}
      <div className="form-section">
        <label htmlFor="name">
          <span className="label-text">Agent Name *</span>
          <input
            id="name"
            type="text"
            name="name"
            value={formData.name}
            onChange={handleChange}
            placeholder="e.g., Tech Sales Team"
            required
            maxLength={255}
            className="form-input"
          />
          <span className="help-text">Unique, memorable name for this agent</span>
        </label>
      </div>

      <div className="form-section">
        <label htmlFor="description">
          <span className="label-text">Description</span>
          <textarea
            id="description"
            name="description"
            value={formData.description}
            onChange={handleChange}
            placeholder="Describe the agent's purpose and focus..."
            rows={4}
            maxLength={1000}
            className="form-input"
          />
          <span className="help-text">
            {formData.description.length}/1000 characters
          </span>
        </label>
      </div>

      <div className="form-row">
        <label htmlFor="agent_type">
          <span className="label-text">Agent Type</span>
          <select
            id="agent_type"
            name="agent_type"
            value={formData.agent_type}
            onChange={handleChange}
            className="form-input"
          >
            <option value="custom">Custom</option>
            <option value="staffing">Staffing</option>
          </select>
        </label>

        <label htmlFor="status">
          <span className="label-text">Status</span>
          <div className="status-radios">
            <label className="radio-label">
              <input
                type="radio"
                name="status"
                value="active"
                checked={formData.status === "active"}
                onChange={handleChange}
              />
              Active
            </label>
            <label className="radio-label">
              <input
                type="radio"
                name="status"
                value="paused"
                checked={formData.status === "paused"}
                onChange={handleChange}
              />
              Paused
            </label>
            <label className="radio-label">
              <input
                type="radio"
                name="status"
                value="archived"
                checked={formData.status === "archived"}
                onChange={handleChange}
              />
              Archived
            </label>
          </div>
        </label>
      </div>

      <div className="form-actions">
        <button
          type="button"
          onClick={() => {
            setHasChanges(false);
            onUnsavedChange(false);
          }}
          className="btn-secondary"
        >
          Cancel
        </button>
        <button type="submit" className="btn-primary" disabled={!hasChanges || saving}>
          {saving ? "Saving..." : "Save Changes"}
        </button>
      </div>

      <style>{`
        .basic-info-form {
          display: flex;
          flex-direction: column;
          gap: 24px;
        }

        .form-section {
          display: flex;
          flex-direction: column;
        }

        .form-section label {
          display: flex;
          flex-direction: column;
          gap: 8px;
        }

        .label-text {
          font-size: 0.85rem;
          font-weight: 700;
          color: var(--ink-1);
          text-transform: uppercase;
          letter-spacing: 0.05em;
        }

        .form-input {
          padding: 12px 16px;
          border: 1px solid var(--border);
          border-radius: 12px;
          font-size: 0.95rem;
          font-family: inherit;
          background: var(--bg-card);
          color: var(--ink-0);
          transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1);
        }

        .form-input:focus {
          outline: none;
          border-color: var(--blue);
          box-shadow: 0 0 0 4px var(--blue-soft);
          background: var(--bg-page);
        }

        .help-text {
          font-size: 0.8rem;
          color: var(--ink-3);
          margin-top: 4px;
        }

        .form-row {
          display: grid;
          grid-template-columns: 1fr 1fr;
          gap: 24px;
        }

        .status-radios {
          display: flex;
          gap: 20px;
          background: var(--bg-subtle);
          padding: 12px;
          border-radius: 12px;
          border: 1px solid var(--border);
        }

        .radio-label {
          display: flex;
          align-items: center;
          gap: 8px;
          font-size: 0.9rem;
          font-weight: 600;
          cursor: pointer;
          color: var(--ink-1);
        }

        .radio-label input {
          width: 18px;
          height: 18px;
          cursor: pointer;
          accent-color: var(--blue);
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
          transition: all 0.2s ease;
          border: 1px solid transparent;
        }

        .btn-primary {
          background: var(--blue);
          color: white;
          box-shadow: 0 4px 12px var(--blue-soft);
        }

        .btn-primary:hover:not(:disabled) {
          transform: translateY(-1px);
          box-shadow: 0 6px 16px var(--blue-soft);
        }

        .btn-primary:disabled {
          opacity: 0.5;
          cursor: not-allowed;
          filter: grayscale(1);
        }

        .btn-secondary {
          background: var(--bg-subtle);
          color: var(--ink-1);
          border-color: var(--border);
        }

        .btn-secondary:hover {
          background: var(--border);
          color: var(--ink-0);
        }

        @media (max-width: 768px) {
          .form-row {
            grid-template-columns: 1fr;
          }
          .form-actions {
            flex-direction: column;
          }
          .btn-primary,
          .btn-secondary {
            width: 100%;
          }
        }
      `}</style>
    </form>
  );
}
