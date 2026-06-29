import { useEffect, useState } from "react";
import { Outlet, useParams } from "react-router-dom";
import { getAgents } from "../api/client";
import type { AgentDefinitionRow } from "../api/types";

export function AgentWorkspace() {
  const { agentId } = useParams<{ agentId: string }>();
  const [agent, setAgent] = useState<AgentDefinitionRow | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const loadAgent = async () => {
      try {
        const response = await getAgents();
        const items = response.items ?? [];
        const found = items.find((a) => a.id === Number(agentId));
        setAgent(found ?? null);
      } catch (err) {
        console.error("Failed to load agent:", err);
      } finally {
        setLoading(false);
      }
    };
    loadAgent();
  }, [agentId]);

  if (loading) {
    return (
      <div className="page-stack">
        <div className="page-header">
          <div className="page-header-left">
            <h1>Agent Workspace</h1>
          </div>
        </div>
        <p>Loading...</p>
      </div>
    );
  }

  if (!agent) {
    return (
      <div className="page-stack">
        <div className="page-header">
          <div className="page-header-left">
            <h1>Agent Not Found</h1>
          </div>
        </div>
        <p>The agent could not be found.</p>
      </div>
    );
  }

  // Determine current tab from pathname
  // const currentTab = location.pathname.split("/").pop() || "companies";

  return (
    <div className="agent-workspace fade-in">
      <div className="workspace-content">
        <Outlet context={{ activeAgentId: agent.id, agentScopeVersion: 0 }} />
      </div>
    </div>
  );
}
