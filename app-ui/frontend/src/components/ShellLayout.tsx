import { useEffect, useState } from "react";
import { NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
import { clearSession, getAgents, getUsername } from "../api/client";
import type { AgentDefinitionRow } from "../api/types";
import { AppIcon, UserDropdown } from "./index";

export function ShellLayout() {
  const navigate = useNavigate();
  const location = useLocation();
  const username = getUsername() ?? "admin";

  const [dark, setDark]                   = useState(() => localStorage.getItem("theme") === "dark");
  const [agents, setAgents]               = useState<AgentDefinitionRow[]>([]);
  const [expandedAgent, setExpandedAgent] = useState<number | null>(null);

  // Apply theme attribute on html element
  useEffect(() => {
    document.documentElement.setAttribute("data-theme", dark ? "dark" : "light");
  }, [dark]);

  // Load agents once on mount
  useEffect(() => {
    getAgents()
      .then((r) => {
        setAgents(r.items ?? []);
        const match = location.pathname.match(/\/agent\/(\d+)/);
        if (match) setExpandedAgent(Number(match[1]));
      })
      .catch(() => setAgents([]));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Keep expanded agent in sync with URL changes
  useEffect(() => {
    const match = location.pathname.match(/\/agent\/(\d+)/);
    if (match) setExpandedAgent(Number(match[1]));
  }, [location.pathname]);

  const toggleTheme = () => {
    const next = !dark;
    setDark(next);
    localStorage.setItem("theme", next ? "dark" : "light");
  };

  const logout = () => {
    clearSession();
    navigate("/login", { replace: true });
  };

  const getBreadcrumb = (): { section: string; page: string } => {
    const p = location.pathname;
    if (p === "/") return { section: "Platform", page: "Overview" };
    const agentMatch = p.match(/\/agent\/(\d+)\/(.+)/);
    if (agentMatch) {
      const agent = agents.find((a) => a.id === Number(agentMatch[1]));
      const seg   = agentMatch[2].split("/")[0];
      const labels: Record<string, string> = {
        dashboard: "Dashboard", companies: "Companies", prospects: "Prospects",
        "lead-sources": "Lead Sources", settings: "Configuration",
      };
      return { section: agent?.name ?? "Agent", page: labels[seg] ?? seg };
    }
    if (p === "/system/runs")     return { section: "System", page: "Run History" };
    if (p === "/system/settings") return { section: "System", page: "Settings" };
    return { section: "Platform", page: "Page" };
  };

  const breadcrumb = getBreadcrumb();

  const agentSubLinks = [
    { path: "dashboard",    label: "Dashboard",     icon: "dashboard" },
    { path: "companies",    label: "Companies",     icon: "companies" },
    { path: "prospects",    label: "Prospects",     icon: "prospects" },
    { path: "lead-sources", label: "Lead Sources",  icon: "leads"     },
    { path: "settings",     label: "Configuration", icon: "settings"  },
  ] as const;

  return (
    <div className="app-shell">
      {/* ── SIDEBAR ── */}
      <aside className="sidebar" role="navigation" aria-label="Main navigation">
        <div className="sidebar-top">
          <div className="brand-logo" aria-hidden="true">
            <AppIcon name="pulse" size={14} />
          </div>
          <span className="brand-name">ProspectOS</span>
        </div>

        <nav className="sidebar-nav">
          {/* Platform */}
          <div className="nav-section">
            <span className="nav-section-label">Platform</span>
            <NavLink
              to="/"
              end
              className={({ isActive }) => `nav-link${isActive ? " active" : ""}`}
            >
              <AppIcon name="dashboard" size={15} className="nav-icon" />
              Overview
            </NavLink>
          </div>

          {/* Agents */}
          <div className="nav-section">
            <span className="nav-section-label">Built-in Agents</span>
            {agents.length === 0 && (
              <span style={{ fontSize:".78rem", color:"var(--text-disabled)", padding:"4px 10px", display:"block" }}>
                No agents registered
              </span>
            )}
            {agents.map((agent) => {
              const isExpanded    = expandedAgent === agent.id;
              const isAgentActive = location.pathname.includes(`/agent/${agent.id}/`);
              return (
                <div key={agent.id} className="agent-nav-group">
                  <button
                    className={`agent-nav-header${isAgentActive ? " active" : ""}`}
                    onClick={() => {
                      setExpandedAgent(isExpanded ? null : agent.id);
                      if (!isExpanded) navigate(`/agent/${agent.id}/dashboard`);
                    }}
                    aria-expanded={isExpanded}
                  >
                    <div className="agent-avatar-sm" aria-hidden="true">
                      {agent.name[0]?.toUpperCase()}
                    </div>
                    <span style={{ flex:1, fontSize:".84rem", fontWeight:650, textAlign:"left" }}>
                      {agent.name}
                    </span>
                    <AppIcon
                      name={isExpanded ? "chevronDown" : "chevronRight"}
                      size={12}
                      style={{ color:"var(--text-disabled)", flexShrink:0 }}
                    />
                  </button>

                  {isExpanded && (
                    <div className="agent-sub-nav">
                      {agentSubLinks.map(({ path, label, icon }) => (
                        <NavLink
                          key={path}
                          to={`/agent/${agent.id}/${path}`}
                          className={({ isActive }) => `nav-link${isActive ? " active" : ""}`}
                        >
                          <AppIcon name={icon} size={13} className="nav-icon" />
                          {label}
                        </NavLink>
                      ))}
                    </div>
                  )}
                </div>
              );
            })}
          </div>

          {/* System */}
          <div className="nav-section">
            <span className="nav-section-label">System</span>
            <NavLink
              to="/system/runs"
              className={({ isActive }) => `nav-link${isActive ? " active" : ""}`}
            >
              <AppIcon name="pulse" size={15} className="nav-icon" />
              Run History
            </NavLink>
            <NavLink
              to="/system/settings"
              className={({ isActive }) => `nav-link${isActive ? " active" : ""}`}
            >
              <AppIcon name="settings" size={15} className="nav-icon" />
              System Settings
            </NavLink>
          </div>
        </nav>

        {/* User dropdown — correct component, placed in footer */}
        <UserDropdown username={username} onLogout={logout} />
      </aside>

      {/* ── CONTENT AREA ── */}
      <div className="content-area">
        <header className="topbar">
          <div className="topbar-left">
            <nav className="breadcrumb" aria-label="Breadcrumb">
              <span className="breadcrumb-section">{breadcrumb.section}</span>
              <span className="breadcrumb-sep" aria-hidden="true">/</span>
              <span className="breadcrumb-page">{breadcrumb.page}</span>
            </nav>
          </div>
          <div className="topbar-right">
            <div className="system-status-pill" aria-label="System live">
              <span className="status-dot" aria-hidden="true" />
              Live
            </div>
            <button
              className="theme-toggle"
              onClick={toggleTheme}
              title={dark ? "Switch to light mode" : "Switch to dark mode"}
              aria-label={dark ? "Switch to light mode" : "Switch to dark mode"}
            >
              <AppIcon name={dark ? "sun" : "moon"} size={14} />
            </button>
          </div>
        </header>

        <main className="main-panel">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
