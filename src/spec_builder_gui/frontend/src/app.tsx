import { BrowserRouter, NavLink, Navigate, Route, Routes } from "react-router-dom";

import { JobDetailPage } from "./pages/job-detail-page";
import { JobsPage } from "./pages/jobs-page";
import { NewJobPage } from "./pages/new-job-page";
import { PastSpecsPage } from "./pages/past-specs-page";

function AppShell() {
  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand-lockup">
          <span className="brand-dot" aria-hidden="true" />
          <div>
            <p className="eyebrow">Local Studio</p>
            <h1>Spec Builder</h1>
          </div>
        </div>

        <nav className="sidebar-nav" aria-label="Primary">
          <NavLink to="/jobs/new" className={({ isActive }) => `nav-link ${isActive ? "is-active" : ""}`}>
            New Job
          </NavLink>
          <NavLink to="/jobs" className={({ isActive }) => `nav-link ${isActive ? "is-active" : ""}`}>
            Jobs
          </NavLink>
          <NavLink to="/specs" className={({ isActive }) => `nav-link ${isActive ? "is-active" : ""}`}>
            Past Specs
          </NavLink>
        </nav>

        <section className="sidebar-player">
          <p className="eyebrow">Worker Reminder</p>
          <h2>Keep the Temporal worker running.</h2>
          <p>
            The local GUI submits jobs and polls state, but the spec builder worker remains its own
            process.
          </p>
        </section>
      </aside>

      <main className="main-panel">
        <Routes>
          <Route path="/" element={<Navigate to="/jobs/new" replace />} />
          <Route path="/jobs/new" element={<NewJobPage />} />
          <Route path="/jobs" element={<JobsPage />} />
          <Route path="/jobs/:jobId" element={<JobDetailPage />} />
          <Route path="/specs" element={<PastSpecsPage />} />
        </Routes>
      </main>
    </div>
  );
}

export function App() {
  return (
    <BrowserRouter
      future={{
        v7_relativeSplatPath: true,
        v7_startTransition: true,
      }}
    >
      <AppShell />
    </BrowserRouter>
  );
}
