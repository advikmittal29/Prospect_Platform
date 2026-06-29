import { useEffect } from "react";
import { Navigate, Route, Routes, useNavigate } from "react-router-dom";
import { clearSession, getToken } from "./api/client";
import { ShellLayout } from "./components/ShellLayout";
import { LoginPage } from "./pages/LoginPage";
import { OverviewPage } from "./pages/OverviewPage";
import { AgentDashboardPage } from "./pages/AgentDashboardPage";
import { AgentCompaniesPage } from "./pages/AgentCompaniesPage";
import { AgentProspectsPage } from "./pages/AgentProspectsPage";
import { AgentLeadSourcesPage } from "./pages/AgentLeadSourcesPage";
import { AgentSettingsPage } from "./pages/AgentSettingsPage";
import { SystemRunsPage } from "./pages/SystemRunsPage";
import { SystemSettingsPage } from "./pages/SystemSettingsPage";

/**
 * ProtectedRoute
 *
 * Guards any subtree that requires an authenticated session.
 *
 * - On initial render: if no token exists redirect immediately to /login.
 * - On token expiry mid-session: the api/client `request()` function calls
 *   `window.location.replace("/login")` on any 401 response, which is the
 *   primary runtime guard. This component handles the cold-start case.
 */
function ProtectedRoute({ children }: { children: JSX.Element }) {
  const navigate = useNavigate();

  useEffect(() => {
    if (!getToken()) {
      clearSession();
      navigate("/login", { replace: true });
    }
  }, [navigate]);

  // Render nothing while the redirect is in-flight to avoid a flash of the
  // protected page content on expired sessions.
  if (!getToken()) {
    return null;
  }

  return children;
}

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />

      <Route
        path="/"
        element={
          <ProtectedRoute>
            <ShellLayout />
          </ProtectedRoute>
        }
      >
        {/* Platform overview */}
        <Route index element={<OverviewPage />} />

        {/* Per-agent routes — each agent is isolated */}
        <Route path="agent/:agentId">
          <Route index element={<Navigate to="dashboard" replace />} />
          <Route path="dashboard"    element={<AgentDashboardPage />} />
          <Route path="companies"    element={<AgentCompaniesPage />} />
          <Route path="prospects"    element={<AgentProspectsPage />} />
          <Route path="lead-sources" element={<AgentLeadSourcesPage />} />
          <Route path="settings"     element={<AgentSettingsPage />} />
        </Route>

        {/* System-level routes */}
        <Route path="system/runs"     element={<SystemRunsPage />} />
        <Route path="system/settings" element={<SystemSettingsPage />} />
      </Route>

      {/* Any unknown path redirects to root, which is itself guarded. */}
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
