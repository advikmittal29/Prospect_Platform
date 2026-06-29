import { useEffect, useState } from "react";
import { getAgentProfile, updateAgentProfile } from "../../api/client";

interface SalesProfileFormProps {
  agentId: number;
  onUnsavedChange: (hasChanges: boolean) => void;
}

export function SalesProfileForm({ agentId, onUnsavedChange }: SalesProfileFormProps) {
  const [formData, setFormData] = useState({
    persona_title: "",
    domain_focus: "",
    service_offering: "",
    sales_objective: "",
    target_buyer_roles: [] as string[],
    value_outcomes: [] as string[],
  });
  const [hasChanges, setHasChanges] = useState(false);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [version, setVersion] = useState(0);

  useEffect(() => {
    let mounted = true;
    const load = async () => {
      try {
        const res = await getAgentProfile(agentId);
        if (!mounted) return;
        const p = res.profile || {};
        setVersion(p.version || 0);
        setFormData({
          persona_title: p.persona_title || "",
          domain_focus: p.domain_focus || "",
          service_offering: p.service_offering || "",
          sales_objective: p.sales_objective || "",
          target_buyer_roles: p.target_buyer_roles || [],
          value_outcomes: p.value_outcomes || [],
        });
      } catch (err) {
        if (!mounted) return;
        setError("Failed to load profile");
      } finally {
        if (mounted) setLoading(false);
      }
    };
    load();
    return () => { mounted = false; };
  }, [agentId]);

  const handleChange = (
    e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>
  ) => {
    const { name, value } = e.target;
    setFormData((prev) => ({ ...prev, [name]: value }));
    setHasChanges(true);
    onUnsavedChange(true);
  };

  const handleAddTag = (field: "target_buyer_roles" | "value_outcomes", tag: string) => {
    if (tag.trim()) {
      setFormData((prev) => ({
        ...prev,
        [field]: [...prev[field], tag],
      }));
      setHasChanges(true);
      onUnsavedChange(true);
    }
  };

  const handleRemoveTag = (field: "target_buyer_roles" | "value_outcomes", index: number) => {
    setFormData((prev) => ({
      ...prev,
      [field]: prev[field].filter((_, i) => i !== index),
    }));
    setHasChanges(true);
    onUnsavedChange(true);
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setSaving(true);
    setError("");
    try {
      const res = await updateAgentProfile(agentId, { ...formData, version });
      setVersion(res.version);
      setHasChanges(false);
      onUnsavedChange(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to update profile");
    } finally {
      setSaving(false);
    }
  };

  if (loading) return <div>Loading sales profile...</div>;

  return (
    <form onSubmit={handleSubmit} className="sales-profile-form">
      {error && <div className="error-banner">{error}</div>}
      <div className="form-section">
        <label htmlFor="persona_title">
          <span className="label-text">Persona Title *</span>
          <input
            id="persona_title"
            type="text"
            name="persona_title"
            value={formData.persona_title}
            onChange={handleChange}
            placeholder="e.g., Enterprise IT Director"
            required
            className="form-input"
          />
        </label>
      </div>

      <div className="form-section">
        <label htmlFor="domain_focus">
          <span className="label-text">Domain Focus *</span>
          <input
            id="domain_focus"
            type="text"
            name="domain_focus"
            value={formData.domain_focus}
            onChange={handleChange}
            placeholder="e.g., Cloud Infrastructure"
            required
            className="form-input"
          />
        </label>
      </div>

      <div className="form-section">
        <label htmlFor="service_offering">
          <span className="label-text">Service Offering *</span>
          <textarea
            id="service_offering"
            name="service_offering"
            value={formData.service_offering}
            onChange={handleChange}
            placeholder="What do you sell?"
            required
            rows={4}
            className="form-input"
          />
        </label>
      </div>

      <div className="form-section">
        <label htmlFor="sales_objective">
          <span className="label-text">Sales Objective *</span>
          <textarea
            id="sales_objective"
            name="sales_objective"
            value={formData.sales_objective}
            onChange={handleChange}
            placeholder="Primary goal for sales..."
            required
            rows={4}
            className="form-input"
          />
        </label>
      </div>

      <div className="form-section">
        <span className="label-text">Target Buyer Roles</span>
        <div className="tags-container">
          {formData.target_buyer_roles.map((tag, idx) => (
            <span key={idx} className="tag">
              {tag}
              <button
                type="button"
                onClick={() => handleRemoveTag("target_buyer_roles", idx)}
                className="tag-remove"
              >
                ✕
              </button>
            </span>
          ))}
          <input
            type="text"
            placeholder="Add role (CTO, VP Eng, etc.)"
            onKeyPress={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                handleAddTag("target_buyer_roles", e.currentTarget.value);
                e.currentTarget.value = "";
              }
            }}
            className="tag-input"
          />
        </div>
      </div>

      <div className="form-section">
        <span className="label-text">Value Outcomes</span>
        <div className="tags-container">
          {formData.value_outcomes.map((tag, idx) => (
            <span key={idx} className="tag">
              {tag}
              <button
                type="button"
                onClick={() => handleRemoveTag("value_outcomes", idx)}
                className="tag-remove"
              >
                ✕
              </button>
            </span>
          ))}
          <input
            type="text"
            placeholder="Add outcome (Cost reduction, etc.)"
            onKeyPress={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                handleAddTag("value_outcomes", e.currentTarget.value);
                e.currentTarget.value = "";
              }
            }}
            className="tag-input"
          />
        </div>
      </div>

      <div className="form-actions">
        <button type="button" className="btn-secondary">
          Cancel
        </button>
        <button type="button" className="btn-secondary">
          Save as Draft
        </button>
        <button type="submit" className="btn-primary" disabled={!hasChanges || saving}>
          {saving ? "Saving..." : "Save Changes"}
        </button>
      </div>

      <style>{`
        .sales-profile-form {
          display: flex;
          flex-direction: column;
          gap: 24px;
        }

        .form-section {
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

        .tags-container {
          display: flex;
          flex-wrap: wrap;
          gap: 10px;
          padding: 16px;
          border: 1px solid var(--border);
          border-radius: 12px;
          background: var(--bg-subtle);
        }

        .tag {
          display: inline-flex;
          align-items: center;
          gap: 8px;
          padding: 6px 14px;
          background: var(--blue-soft);
          color: var(--blue);
          border-radius: 999px;
          font-size: 0.85rem;
          font-weight: 700;
          border: 1px solid var(--blue-soft);
          transition: all 0.2s;
        }

        .tag:hover {
          border-color: var(--blue);
        }

        .tag-remove {
          background: none;
          border: none;
          color: inherit;
          cursor: pointer;
          font-size: 14px;
          display: flex;
          align-items: center;
          justify-content: center;
          padding: 0;
          opacity: 0.6;
        }
        
        .tag-remove:hover { opacity: 1; }

        .tag-input {
          flex: 1;
          min-width: 200px;
          border: none;
          background: transparent;
          outline: none;
          font-size: 0.9rem;
          font-family: inherit;
          color: var(--ink-0);
          padding: 4px;
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
      `}</style>
    </form>
  );
}
