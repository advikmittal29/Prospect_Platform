import { useEffect, useState } from "react";
import { getAgentProfile, updateAgentProfile } from "../../api/client";

interface ChannelPolicyFormProps {
  agentId: number;
  onUnsavedChange: (hasChanges: boolean) => void;
}

export function ChannelPolicyForm({ agentId, onUnsavedChange }: ChannelPolicyFormProps) {
  const [formData, setFormData] = useState({
    preferred_channels: ["linkedin_dm", "email"],
    email_from: "",
    email_signature: "",
    message_template: "default",
    suppression_enabled: true,
    bounce_list_enabled: true,
    cooldown_period_days: 3,
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
        const policy = p.channel_policy || {};
        setVersion(p.version || 0);
        setFormData({
          preferred_channels: (policy.preferred_channels as string[]) || ["linkedin_dm", "email"],
          email_from: (policy.email_from as string) || "",
          email_signature: (policy.email_signature as string) || "",
          message_template: (policy.message_template as string) || "default",
          suppression_enabled: (policy.suppression_enabled as boolean) ?? true,
          bounce_list_enabled: (policy.bounce_list_enabled as boolean) ?? true,
          cooldown_period_days: (policy.cooldown_period_days as number) || 3,
        });
      } catch (err) {
        if (!mounted) return;
        setError("Failed to load channel policy");
      } finally {
        if (mounted) setLoading(false);
      }
    };
    load();
    return () => { mounted = false; };
  }, [agentId]);

  const handleChange = (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement>) => {
    const { name, value, type } = e.target;
    if (type === "checkbox") {
      const checked = (e.target as HTMLInputElement).checked;
      setFormData((prev) => ({ ...prev, [name]: checked }));
    } else {
      setFormData((prev) => ({ ...prev, [name]: value }));
    }
    setHasChanges(true);
    onUnsavedChange(true);
  };

  const handleChannelToggle = (channel: string) => {
    setFormData((prev) => ({
      ...prev,
      preferred_channels: prev.preferred_channels.includes(channel)
        ? prev.preferred_channels.filter((c) => c !== channel)
        : [...prev.preferred_channels, channel],
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
        channel_policy: formData 
      });
      setVersion(updatedProfile.version);
      setHasChanges(false);
      onUnsavedChange(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to update channel policy");
    } finally {
      setSaving(false);
    }
  };

  if (loading) return <div>Loading channel policy...</div>;

  return (
    <form onSubmit={handleSubmit} className="channel-policy-form">
      {error && <div className="error-banner">{error}</div>}
      <div className="form-section">
        <span className="label-text">Preferred Outreach Channels</span>
        <div className="channels-grid">
          {["linkedin_dm", "email", "phone", "sms"].map((channel) => (
            <label key={channel} className="channel-checkbox">
              <input
                type="checkbox"
                checked={formData.preferred_channels.includes(channel)}
                onChange={() => handleChannelToggle(channel)}
              />
              <span className="channel-label">
                {channel === "linkedin_dm"
                  ? "LinkedIn DM"
                  : channel === "email"
                    ? "Email"
                    : channel === "phone"
                      ? "Phone"
                      : "SMS"}
              </span>
            </label>
          ))}
        </div>
      </div>

      <div className="form-section">
        <label htmlFor="email_from">
          <span className="label-text">From Email</span>
          <input
            id="email_from"
            type="email"
            name="email_from"
            value={formData.email_from}
            onChange={handleChange}
            placeholder="your-email@company.com"
            className="form-input"
          />
        </label>
      </div>

      <div className="form-section">
        <label htmlFor="email_signature">
          <span className="label-text">Email Signature</span>
          <textarea
            id="email_signature"
            name="email_signature"
            value={formData.email_signature}
            onChange={handleChange}
            placeholder="Your name, title, and contact information"
            rows={4}
            className="form-input"
          />
        </label>
      </div>

      <div className="form-section">
        <label htmlFor="message_template">
          <span className="label-text">Message Template</span>
          <select
            id="message_template"
            name="message_template"
            value={formData.message_template}
            onChange={handleChange}
            className="form-input"
          >
            <option value="default">Default Template</option>
            <option value="formal">Formal Template</option>
            <option value="casual">Casual Template</option>
            <option value="custom">Custom Template</option>
          </select>
          <span className="help-text">[Create/Edit Template]</span>
        </label>
      </div>

      <div className="form-section">
        <span className="label-text">Suppression & Compliance</span>
        <div className="compliance-options">
          <label className="compliance-checkbox">
            <input
              type="checkbox"
              name="suppression_enabled"
              checked={formData.suppression_enabled}
              onChange={handleChange}
            />
            <span>Do Not Contact Registry</span>
          </label>
          <label className="compliance-checkbox">
            <input
              type="checkbox"
              name="bounce_list_enabled"
              checked={formData.bounce_list_enabled}
              onChange={handleChange}
            />
            <span>Bounce List Management</span>
          </label>
        </div>
      </div>

      <div className="form-section">
        <label htmlFor="cooldown_period">
          <span className="label-text">Cooldown Period (days)</span>
          <input
            id="cooldown_period"
            type="number"
            name="cooldown_period_days"
            value={formData.cooldown_period_days}
            onChange={handleChange}
            min="1"
            max="30"
            className="form-input"
          />
          <span className="help-text">Wait period before re-contacting same person</span>
        </label>
      </div>

      <div className="form-actions">
        <button type="button" className="btn-secondary">
          Cancel
        </button>
        <button type="submit" className="btn-primary" disabled={!hasChanges || saving}>
          {saving ? "Saving..." : "Save Policy"}
        </button>
      </div>

      <style>{`
        .channel-policy-form {
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

        .channels-grid {
          display: grid;
          grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
          gap: 12px;
        }

        .channel-checkbox {
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
        
        .channel-checkbox:hover { border-color: var(--blue); background: var(--blue-soft); }

        .channel-checkbox input {
          width: 18px;
          height: 18px;
          accent-color: var(--blue);
        }

        .channel-label {
          font-size: 0.9rem;
          font-weight: 600;
          color: var(--ink-1);
        }

        .compliance-options {
          display: flex;
          flex-direction: column;
          gap: 12px;
        }

        .compliance-checkbox {
          display: flex;
          align-items: center;
          gap: 12px;
          cursor: pointer;
          padding: 16px;
          background: var(--bg-subtle);
          border: 1px solid var(--border);
          border-radius: 12px;
          transition: all 0.2s;
        }
        
        .compliance-checkbox:hover { border-color: var(--blue); }

        .compliance-checkbox input {
          width: 18px;
          height: 18px;
          accent-color: var(--blue);
        }
        
        .compliance-checkbox span {
          font-size: 0.95rem;
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

        .btn-secondary {
          background: var(--bg-subtle);
          border-color: var(--border);
          color: var(--ink-1);
        }
      `}</style>
    </form>
  );
}
