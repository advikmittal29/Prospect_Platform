import type { AgentDefinitionRow } from "../../api/types";
import { AgentNode } from "./AgentNode";

interface TreeViewProps {
  agents: AgentDefinitionRow[];
  expandedAgents: Set<number>;
  activeAgentId: number | null;
  loading: boolean;
  error: string | null;
  onToggleAgent: (agentId: number) => void;
  onSelectAgent: (agentId: number) => void;
  onRetry?: () => void;
}

export function TreeView({
  agents,
  expandedAgents,
  activeAgentId,
  loading,
  error,
  onToggleAgent,
  onSelectAgent,
  onRetry,
}: TreeViewProps) {
  if (loading) {
    return (
      <div className="tree-view loading">
        <div className="skeleton-item" style={{ height: "32px", marginBottom: "8px" }} />
        <div className="skeleton-item" style={{ height: "32px", marginBottom: "8px" }} />
        <div className="skeleton-item" style={{ height: "32px" }} />
      </div>
    );
  }

  if (error) {
    return (
      <div className="tree-view error">
        <div style={{ padding: "12px", fontSize: "0.85rem", color: "var(--red-500)" }}>
          <p>Failed to load agents</p>
          <p style={{ marginTop: "8px", color: "var(--ink-2)", fontSize: "0.8rem" }}>
            {error}
          </p>
          {onRetry && (
            <button
              onClick={onRetry}
              style={{
                marginTop: "8px",
                padding: "4px 12px",
                fontSize: "0.8rem",
                background: "var(--ink-3)",
                border: "1px solid var(--ink-2)",
                borderRadius: "4px",
                cursor: "pointer",
              }}
            >
              Retry
            </button>
          )}
        </div>
      </div>
    );
  }

  if (agents.length === 0) {
    return (
      <div className="tree-view empty">
        <div style={{ padding: "12px", fontSize: "0.85rem", color: "var(--ink-2)" }}>
          <p>No agents yet</p>
          <p style={{ marginTop: "4px", fontSize: "0.8rem" }}>Create one to get started</p>
        </div>
      </div>
    );
  }

  return (
    <div className="tree-view">
      {agents.map((agent) => (
        <AgentNode
          key={agent.id}
          agent={agent}
          isExpanded={expandedAgents.has(agent.id)}
          isActive={activeAgentId === agent.id}
          onToggle={onToggleAgent}
          onSelect={onSelectAgent}
        />
      ))}
    </div>
  );
}
