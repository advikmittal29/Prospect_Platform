import { useCallback } from "react";
import type { AgentDefinitionRow } from "../../api/types";
import { AppIcon } from "../AppIcon";
import { AgentMenuItems } from "./AgentMenuItems";

interface AgentNodeProps {
  agent: AgentDefinitionRow;
  isExpanded: boolean;
  isActive: boolean;
  onToggle: (agentId: number) => void;
  onSelect: (agentId: number) => void;
}

export function AgentNode({
  agent,
  isExpanded,
  isActive,
  onToggle,
  onSelect,
}: AgentNodeProps) {
  const handleToggleClick = useCallback(
    (e: React.MouseEvent) => {
      e.preventDefault();
      e.stopPropagation();
      onToggle(agent.id);
    },
    [agent.id, onToggle]
  );

  const handleNodeClick = useCallback(
    (e: React.MouseEvent) => {
      e.preventDefault();
      onSelect(agent.id);
    },
    [agent.id, onSelect]
  );

  const statusColor = {
    active: "#10B981",
    paused: "#F59E0B",
    archived: "#6B7280",
  }[agent.status] || "#6B7280";

  return (
    <div className={`agent-node ${isActive ? "active" : ""}`}>
      <div className="agent-node-header" onClick={handleNodeClick}>
        <button
          className="agent-node-toggle"
          onClick={handleToggleClick}
          aria-label={isExpanded ? "Collapse agent" : "Expand agent"}
          aria-expanded={isExpanded}
        >
          <svg
            width="12"
            height="12"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            style={{
              transform: isExpanded ? "rotate(90deg)" : "rotate(0deg)",
              transition: "transform 0.2s ease",
            }}
          >
            <polyline points="9 18 15 12 9 6" />
          </svg>
        </button>

        <AppIcon name="agents" size={13} className="agent-node-icon" />

        <span className="agent-node-name">{agent.name}</span>

        <span
          className="agent-node-status"
          style={{
            backgroundColor: statusColor,
            width: "6px",
            height: "6px",
            borderRadius: "50%",
            display: "inline-block",
            marginLeft: "auto",
          }}
          title={`Status: ${agent.status}`}
        />
      </div>

      {isExpanded && <AgentMenuItems agent={agent} isExpanded={isExpanded} />}
    </div>
  );
}
