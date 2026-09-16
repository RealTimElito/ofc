import { NavLink, Route, Routes, useLocation } from "react-router-dom";
import AboutPage from "./pages/AboutPage";
import ReportsPage from "./pages/ReportsPage";
import ReportEditorPage from "./pages/ReportEditorPage";
import SourcesPage from "./pages/SourcesPage";
import SettingsPage from "./pages/SettingsPage";

function MainShell() {
  const location = useLocation();
  const fluid = location.pathname.startsWith("/reports/");
  return (
    <main className={fluid ? "main main-fluid" : "main"}>
      <Routes>
        <Route path="/" element={<ReportsPage />} />
        <Route path="/reports/:id" element={<ReportEditorPage />} />
        <Route path="/sources" element={<SourcesPage />} />
        <Route path="/settings" element={<SettingsPage />} />
        <Route path="/about" element={<AboutPage />} />
      </Routes>
    </main>
  );
}

export default function App() {
  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          OFC
          <span>Offline Report Composer</span>
        </div>
        <nav className="nav">
          <NavLink to="/" end>
            Reports
          </NavLink>
          <NavLink to="/sources">Sources</NavLink>
          <NavLink to="/settings">LLM &amp; settings</NavLink>
          <NavLink to="/about">About</NavLink>
        </nav>
        <div className="airgap-badge">Air-gapped mode · local endpoints only</div>
      </aside>
      <MainShell />
    </div>
  );
}
