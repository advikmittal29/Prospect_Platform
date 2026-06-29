import { useCallback, useEffect, useState } from "react";

const TREE_STATE_KEY = "agent_tree_state";

/**
 * Hook to manage tree view state with localStorage persistence
 */
export function useTreeViewState() {
  const [expandedAgents, setExpandedAgents] = useState<Set<number>>(() => {
    try {
      const saved = localStorage.getItem(TREE_STATE_KEY);
      if (saved) {
        return new Set(JSON.parse(saved));
      }
    } catch {
      // Ignore parse errors
    }
    return new Set();
  });

  // Persist to localStorage whenever state changes
  useEffect(() => {
    localStorage.setItem(TREE_STATE_KEY, JSON.stringify(Array.from(expandedAgents)));
  }, [expandedAgents]);

  const toggleAgent = useCallback((agentId: number) => {
    setExpandedAgents((prev) => {
      const next = new Set(prev);
      if (next.has(agentId)) {
        next.delete(agentId);
      } else {
        next.add(agentId);
      }
      return next;
    });
  }, []);

  const expandAgent = useCallback((agentId: number) => {
    setExpandedAgents((prev) => {
      const next = new Set(prev);
      next.add(agentId);
      return next;
    });
  }, []);

  const collapseAgent = useCallback((agentId: number) => {
    setExpandedAgents((prev) => {
      const next = new Set(prev);
      next.delete(agentId);
      return next;
    });
  }, []);

  return {
    expandedAgents,
    toggleAgent,
    expandAgent,
    collapseAgent,
  };
}
