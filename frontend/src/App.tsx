import { Link, Route, Routes } from "react-router-dom";
import Dashboard from "./pages/Dashboard";
import DataQuality from "./pages/DataQuality";

function App() {
  return (
    <>
      <nav style={{ display: "flex", gap: 16, padding: 16 }}>
        <Link to="/">Dashboard</Link>
        <Link to="/data-quality">Data Quality</Link>
      </nav>
      <Routes>
        <Route path="/" element={<Dashboard />} />
        <Route path="/data-quality" element={<DataQuality />} />
      </Routes>
    </>
  );
}

export default App;
