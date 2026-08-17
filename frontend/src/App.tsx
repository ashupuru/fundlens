import { NavLink, Route, Routes } from "react-router-dom";
import Dashboard from "./pages/Dashboard";
import DataQuality from "./pages/DataQuality";
import DezervImport from "./pages/DezervImport";
import FundDetail from "./pages/FundDetail";
import Funds from "./pages/Funds";
import Insights from "./pages/Insights";
import Upload from "./pages/Upload";

function DashboardIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
      <rect x="1.5" y="1.5" width="6" height="6" rx="1.5" stroke="currentColor" strokeWidth="1.3" />
      <rect x="8.5" y="1.5" width="6" height="9" rx="1.5" stroke="currentColor" strokeWidth="1.3" />
      <rect x="1.5" y="9.5" width="6" height="5" rx="1.5" stroke="currentColor" strokeWidth="1.3" />
    </svg>
  );
}

function FundsIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
      <path
        d="M2 12.5 6 7l3 3 5-6"
        stroke="currentColor"
        strokeWidth="1.4"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <path d="M2 14.5h12" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
    </svg>
  );
}

function DataQualityIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
      <circle cx="8" cy="8" r="6.25" stroke="currentColor" strokeWidth="1.3" />
      <path d="M8 5v3.2l2 1.8" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" />
    </svg>
  );
}

function InsightsIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
      <path
        d="M8 1.5a4.5 4.5 0 0 0-2.5 8.24V11.5h5V9.74A4.5 4.5 0 0 0 8 1.5Z"
        stroke="currentColor"
        strokeWidth="1.3"
        strokeLinejoin="round"
      />
      <path d="M6 13.5h4M6.5 15h3" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" />
    </svg>
  );
}

function UploadIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
      <path
        d="M8 10.5V2.5M8 2.5 5 5.5M8 2.5l3 3"
        stroke="currentColor"
        strokeWidth="1.4"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <path
        d="M2.5 10.5v2a1 1 0 0 0 1 1h9a1 1 0 0 0 1-1v-2"
        stroke="currentColor"
        strokeWidth="1.4"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

const links = [
  { to: "/", label: "Dashboard", icon: DashboardIcon, end: true },
  { to: "/insights", label: "Insights", icon: InsightsIcon, end: false },
  { to: "/funds", label: "Funds", icon: FundsIcon, end: false },
  { to: "/upload", label: "Upload", icon: UploadIcon, end: false },
  { to: "/data-quality", label: "Data Quality", icon: DataQualityIcon, end: false },
  { to: "/import/dezerv", label: "Import Dezerv CSV", icon: UploadIcon, end: false },
];

function App() {
  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="sidebar-brand">FundLens</div>
        {links.map(({ to, label, icon: Icon, end }) => (
          <NavLink
            key={to}
            to={to}
            end={end}
            className={({ isActive }) => `sidebar-link${isActive ? " active" : ""}`}
          >
            <Icon />
            {label}
          </NavLink>
        ))}
      </aside>
      <div className="app-main">
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/insights" element={<Insights />} />
          <Route path="/funds" element={<Funds />} />
          <Route path="/funds/:amfiCode" element={<FundDetail />} />
          <Route path="/upload" element={<Upload />} />
          <Route path="/data-quality" element={<DataQuality />} />
          <Route path="/import/dezerv" element={<DezervImport />} />
        </Routes>
      </div>
    </div>
  );
}

export default App;
