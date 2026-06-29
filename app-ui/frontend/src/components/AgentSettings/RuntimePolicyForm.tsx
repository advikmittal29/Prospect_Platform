import { useEffect, useState } from "react";
import { getAgentProfile, updateAgentProfile } from "../../api/client";

interface RuntimePolicyFormProps {
  agentId: number;
  onUnsavedChange: (hasChanges: boolean) => void;
}

export function RuntimePolicyForm({ agentId, onUnsavedChange }: RuntimePolicyFormProps) {
  const [formData, setFormData] = useState({
    max_api_calls_per_run: 100,
    monthly_budget_limit: 5000,
    min_confidence_threshold: 0.75,
    rate_limiting_rpm: 30,
    concurrent_tasks: 5,
    enabled_tools: ["linkedin", "apollo", "company_discovery"],
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
        const policy = p.runtime_policy || {};
        setVersion(p.version || 0);
        setFormData({
          max_api_calls_per_run: (policy.max_api_calls_per_run as number) || 100,
          monthly_budget_limit: (policy.monthly_budget_limit as number) || 5000,
          min_confidence_threshold: (policy.min_confidence_threshold as number) || 0.75,
          rate_limiting_rpm: (policy.rate_limiting_rpm as number) || 30,
          concurrent_tasks: (policy.concurrent_tasks as number) || 5,
          enabled_tools: (policy.enabled_tools as string[]) || ["linkedin", "apollo", "company_discovery"],
        });
      } catch (err) {
        if (!mounted) return;
        setError("Failed to load runtime policy");
      } finally {
        if (mounted) setLoading(false);
      }
    };
    load();
    return () => { mounted = false; };
  }, [agentId]);

  const handleChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const { name, value, type } = e.target;
    setFormData((prev) => ({
      ...prev,
      [name]: type === "number" ? parseFloat(value) : value,
    }));
    setHasChanges(true);
    onUnsavedChange(true);
  };

  const handleToolToggle = (tool: string) => {
    setFormData((prev) => ({
      ...prev,
      enabled_tools: prev.enabled_tools.includes(tool)
        ? prev.enabled_tools.filter((t) => t !== tool)
        : [...prev.enabled_tools, tool],
    }));
    setHasChanges(true);
    onUnsavedChange(true);
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setSaving(true);
    setError("");
    try {
      const res = await getAgentProfile(agentId);
      const currentProfile = res.profile || {};
      const updatedProfile = await updateAgentProfile(agentId, { 
        ...currentProfile, 
        version,
        runtime_policy: formData 
      });
      setVersion(updatedProfile.version);
      setHasChanges(false);
      onUnsavedChange(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to update runtime policy");
    } finally {
      setSaving(false);
    }
  };

  if (loading) return <div>Loading runtime policy...</div>;

  const budgetPercentage = Math.min((5000 - formData.monthly_budget_limit) / 50, 100);

  return (
    <form onSubmit={handleSubmit} className="runtime-policy-form">
      {error && <div className="error-banner">{error}</div>}
      <div className="form-section">
        <label htmlFor="max_api_calls">
          <span className="label-text">Max API Calls Per Run</span>
          <input
            id="max_api_calls"
            type="number"
            name="max_api_calls_per_run"
            value={formData.max_api_calls_per_run}
            onChange={handleChange}
            min="10"
            max="1000"
            className="form-input"
          />
          <span className="help-text">Default: 100</span>
        </label>
      </div>

      <div className="form-section">
        <label htmlFor="monthly_budget">
          <span className="label-text">Monthly Budget Limit ($)</span>
          <input
            id="monthly_budget"
            type="number"
            name="monthly_budget_limit"
            value={formData.monthly_budget_limit}
            onChange={handleChange}
            min="100"
            max="50000"
            className="form-input"
          />
          <span className="help-text">Default: $5,000</span>
          {budgetPercentage > 80 && (
            <span className="warning-text">⚠️ Budget usage is at {budgetPercentage.toFixed(0)}%</span>
          )}
        </label>
      </div>

      <div className="form-section">
        <label htmlFor="confidence_threshold">
          <span className="label-text">Min Confidence Threshold</span>
          <div className="slider-container">
            <input
              id="confidence_threshold"
              type="range"
              name="min_confidence_threshold"
              value={formData.min_confidence_threshold}
              onChange={handleChange}
              min="0"
              max="1"
              step="0.05"
              className="form-slider"
            />
            <span className="slider-value">{formData.min_confidence_threshold.toFixed(2)}</span>
          </div>
          <span className="help-text">Threshold between 0.0 and 1.0 (default: 0.75)</span>
        </label>
      </div>

      <div className="form-section">
        <label htmlFor="rate_limiting">
          <span className="label-text">Requests Per Minute</span>
          <input
            id="rate_limiting"
            type="number"
            name="rate_limiting_rpm"
            value={formData.rate_limiting_rpm}
            onChange={handleChange}
            min="1"
            max="100"
            className="form-input"
          />
        </label>
      </div>

      <div className="form-section">
        <label htmlFor="concurrent_tasks">
          <span className="label-text">Concurrent Tasks</span>
          <input
            id="concurrent_tasks"
            type="number"
            name="concurrent_tasks"
            value={formData.concurrent_tasks}
            onChange={handleChange}
            min="1"
            max="50"
            className="form-input"
          />
        </label>
      </div>

      <div className="form-section">
        <span className="label-text">Enabled Tools</span>
        <div className="tools-grid">
          {["linkedin", "apollo", "company_discovery", "email_verify"].map((tool) => (
            <label key={tool} className="tool-checkbox">
              <input
                type="checkbox"
                checked={formData.enabled_tools.includes(tool)}
                onChange={() => handleToolToggle(tool)}
              />
              <span className="tool-label">
                {tool === "linkedin"
                  ? "LinkedIn Search"
                  : tool === "apollo"
                    ? "Apollo Enrichment"
                    : tool === "company_discovery"
                      ? "Company Discovery"
                      : "Email Verification"}
              </span>
            </label>
          ))}
        </div>
      </div>

      <div className="form-actions">
        <button type="button" className="btn-secondary">
          Cancel
        </button>
        <button type="button" className="btn-secondary">
          Reset to Default
        </button>
        <button type="submit" className="btn-primary" disabled={!hasChanges || saving}>
          {saving ? "Saving..." : "Save Policy"}
        </button>
      </div>

      <style>{`
        .runtime-policy-form {
          display: flex;
          flex-direction: column;
          gap: 24px;
        }

        .form-section {
          display: flex;
          flex-direction: column;
          gap: 12px;
          padding-bottom: 24px;
          border-bottom: 1px solid var(--border);
        }
        
        .form-section:last-of-type { border-bottom: none; }

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
          transition: all 0.2s;
        }

        .form-input:focus {
          outline: none;
          border-color: var(--blue);
          box-shadow: 0 0 0 4px var(--blue-soft);
        }

        .help-text {
          font-size: 0.8rem;
          color: var(--ink-3);
        }

        .warning-text {
          font-size: 0.8rem;
          font-weight: 700;
          color: var(--danger);
          margin-top: 4px;
        }

        .slider-container {
          display: flex;
          align-items: center;
          gap: 16px;
          background: var(--bg-subtle);
          padding: 12px 16px;
          border-radius: 12px;
          border: 1px solid var(--border);
        }

        .form-slider {
          flex: 1;
          height: 6px;
          -webkit-appearance: none;
          appearance: none;
          background: var(--border);
          border-radius: 3px;
          outline: none;
        }

        .form-slider::-webkit-slider-thumb {
          -webkit-appearance: none;
          appearance: none;
          width: 20px;
          height: 20px;
          border-radius: 50%;
          background: var(--blue);
          cursor: pointer;
          border: 3px solid white;
          box-shadow: 0 2px 8px rgba(0,0,0,0.2);
        }

        .slider-value {
          min-width: 48px;
          text-align: right;
          font-size: 1rem;
          font-weight: 800;
          color: var(--blue);
          font-family: var(--font-mono);
        }

        .tools-grid {
          display: grid;
          grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
          gap: 12px;
        }

        .tool-checkbox {
          display: flex;
          align-items: center;
          gap: 12px;
          cursor: pointer;
          padding: 12px 16px;
          background: var(--bg-subtle);
          border: 1px solid var(--border);
          border-radius: 12px;
          transition: all 0.2s;
        }
        
        .tool-checkbox:hover { border-color: var(--blue); background: var(--blue-soft); }

        .tool-checkbox input {
          width: 18px;
          height: 18px;
          accent-color: var(--blue);
        }

        .tool-label {
          font-size: 0.9rem;
          font-weight: 600;
          color: var(--ink-1);
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
