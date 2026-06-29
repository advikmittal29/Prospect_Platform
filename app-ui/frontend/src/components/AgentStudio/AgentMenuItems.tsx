import { NavLink } from "react-router-dom";
import type { AgentDefinitionRow } from "../../api/types";
import { AppIcon } from "../AppIcon";

interface AgentMenuItemsProps {
  agent: AgentDefinitionRow;
  isExpanded: boolean;
}

const menuItems = [
  {
    path: "dashboard",
    label: "Dashboard",
    icon: "dashboard" as const,
  },
  {
    path: "companies",
    label: "Companies",
    icon: "companies" as const,
  },
  {
    path: "prospects",
    label: "Prospects",
    icon: "prospects" as const,
  },
  {
    path: "lead-sources",
    label: "Lead Sources",
    icon: "spark" as const,
  },
  {
    path: "settings",
    label: "Settings",
    icon: "settings" as const,
  },
];

export function AgentMenuItems({ agent, isExpanded }: AgentMenuItemsProps) {
  if (!isExpanded) return null;

  return (
    <div className="agent-menu-items">
      {menuItems.map((item) => (
        <NavLink
          key={item.path}
          to={`/agent/${agent.id}/${item.path}`}
          className={({ isActive }) => `agent-menu-item ${isActive ? "active" : ""}`}
        >
          <AppIcon name={item.icon} size={13} className="menu-item-icon" />
          <span className="menu-item-label">{item.label}</span>
        </NavLink>
      ))}
    </div>
  );
}
