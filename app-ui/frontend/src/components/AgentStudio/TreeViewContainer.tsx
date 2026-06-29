import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { getActiveAgentId, getAgents } from "../../api/client";
import type { AgentDefinitionRow } from "../../api/types";
import { useTreeViewState } from "../../hooks/useTreeViewState";
import { TreeView } from "./TreeView";

interface TreeViewContainerProps {
  onAgentSelected?: (agentId: number) => void;
}

export function TreeViewContainer({ onAgentSelected }: TreeViewContainerProps) {
  const navigate = useNavigate();
  const { expandedAgents, toggleAgent, expandAgent } = useTreeViewState();
  const [agents, setAgents] = useState<AgentDefinitionRow[]>([]);
  const [activeAgentId, setActiveAgentId] = useState<number | null>(getActiveAgentId());
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Load agents on mount
  useEffect(() => {
    loadAgents();
  }, []);

  const loadAgents = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const response = await getAgents();
      const items = response.items ?? [];
      setAgents(items);

      // Set active agent
      const saved = getActiveAgentId();
      const activeFromDb = items.find((a) => a.status === "active");
      const fallback = activeFromDb?.id ?? items[0]?.id ?? null;
      const nextActive = saved && items.some((a) => a.id === saved) ? saved : fallback;

      if (nextActive) {
        setActiveAgentId(nextActive); // Update local state
        setActiveAgentId(nextActive); // Update global state via API client
        // Auto-expand the active agent
        expandAgent(nextActive);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load agents");
    } finally {
      setLoading(false);
    }
  }, [expandAgent]);

  const handleSelectAgent = useCallback(
    (agentId: number) => {
      // Update both local and global state
      setActiveAgentId(agentId);
      setActiveAgentId(agentId);
      // Notify parent component
      onAgentSelected?.(agentId);
      // Navigate to dashboard by default
      navigate(`/agent/${agentId}/dashboard`);
      // Auto-expand when selected
      expandAgent(agentId);
    },
    [navigate, onAgentSelected, expandAgent]
  );

  return (
    <TreeView
      agents={agents}
      expandedAgents={expandedAgents}
      activeAgentId={activeAgentId}
      loading={loading}
      error={error}
      onToggleAgent={toggleAgent}
      onSelectAgent={handleSelectAgent}
      onRetry={loadAgents}
    />
  );
}
