import { useEffect, useState } from "react";
import { Cell, Pie, PieChart, ResponsiveContainer, Tooltip } from "recharts";
import {
  getAllocationDrift,
  getCategoryBreakdown,
  getHealth,
  getPortfolioBenchmarkComparison,
  getPortfolioMetrics,
} from "../api/client";
import type { components } from "../api/schema";

type ApiStatus = "checking" | "connected" | "unreachable";
type PortfolioMetrics = components["schemas"]["PortfolioMetricsResponse"];
type PortfolioBenchmarkComparison = components["schemas"]["PortfolioBenchmarkComparisonResponse"];
type CategoryGroup = components["schemas"]["CategoryBreakdownItemResponse"];
type AllocationDrift = components["schemas"]["AllocationDriftResponse"];

// Cycles across chart slices/tiles — chosen to harmonize with the existing
// Monarch-style green/cream theme rather than the raw palette of any
// particular reference design.
const CHART_COLORS = [
  "#1b4332", // brand green
  "#3a6ea5", // blue
  "#c98a2c", // amber
  "#6b4f8a", // purple
  "#2f8a7a", // teal
  "#b3402c", // rust
  "#8a7a2f", // olive
  "#5c6470", // slate
];

function formatPct(value: number | null): string {
  if (value === null) return "—";
  return `${(value * 100).toFixed(2)}%`;
}

function formatGroupPct(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return `${value.toFixed(1)}%`;
}

function formatAlpha(value: number | null): string {
  if (value === null) return "—";
  const sign = value >= 0 ? "+" : "";
  return `${sign}${value.toFixed(2)}%`;
}

function formatPaise(paise: number): string {
  return `₹${(paise / 100).toLocaleString("en-IN", { maximumFractionDigits: 0 })}`;
}

function formatXirr(value: number | null, reason?: string | null): string {
  if (value === null) return reason ? `n/a (${reason})` : "n/a";
  return formatPct(value);
}

function formatDriftPct(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  const sign = value >= 0 ? "+" : "";
  return `${sign}${value.toFixed(1)}%`;
}

function AssetAllocationSection({ groups }: { groups: CategoryGroup[] | null }) {
  if (groups === null) {
    return (
      <div className="card">
        <h3>Asset Allocation</h3>
        <p className="muted">Loading…</p>
      </div>
    );
  }

  if (groups.length === 0) {
    return (
      <div className="card">
        <h3>Asset Allocation</h3>
        <p className="muted">No priced holdings yet.</p>
      </div>
    );
  }

  return (
    <div className="card">
      <h3>Asset Allocation</h3>
      <div style={{ display: "flex", flexWrap: "wrap", gap: 24, alignItems: "center" }}>
        <div style={{ width: 220, height: 220, flexShrink: 0 }}>
          <ResponsiveContainer>
            <PieChart>
              <Pie
                data={groups}
                dataKey="allocation_pct"
                nameKey="label"
                innerRadius={55}
                outerRadius={90}
                paddingAngle={2}
              >
                {groups.map((g, i) => (
                  <Cell key={g.label} fill={CHART_COLORS[i % CHART_COLORS.length]} />
                ))}
              </Pie>
              <Tooltip formatter={(value) => `${Number(value).toFixed(1)}%`} />
            </PieChart>
          </ResponsiveContainer>
        </div>
        <table style={{ flex: 1, minWidth: 240 }}>
          <thead>
            <tr>
              <th>Category</th>
              <th>Allocation</th>
              <th>XIRR</th>
            </tr>
          </thead>
          <tbody>
            {groups.map((g, i) => (
              <tr key={g.label}>
                <td>
                  <span
                    style={{
                      display: "inline-block",
                      width: 10,
                      height: 10,
                      borderRadius: "50%",
                      background: CHART_COLORS[i % CHART_COLORS.length],
                      marginRight: 8,
                    }}
                  />
                  {g.label}
                </td>
                <td>{formatGroupPct(g.allocation_pct)}</td>
                <td className={g.xirr !== null ? (g.xirr >= 0 ? "positive" : "negative") : ""}>
                  {formatXirr(g.xirr, g.xirr_reason)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function CategoryAllocationSection({ groups }: { groups: CategoryGroup[] | null }) {
  if (groups === null) {
    return (
      <div className="card">
        <h3>Category Allocation</h3>
        <p className="muted">Loading…</p>
      </div>
    );
  }

  if (groups.length === 0) {
    return (
      <div className="card">
        <h3>Category Allocation</h3>
        <p className="muted">No priced holdings yet.</p>
      </div>
    );
  }

  return (
    <div className="card">
      <h3>Category Allocation</h3>
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fill, minmax(150px, 1fr))",
          gap: 12,
        }}
      >
        {groups.map((g, i) => (
          <div
            key={g.label}
            style={{
              background: CHART_COLORS[i % CHART_COLORS.length],
              borderRadius: "var(--radius-sm)",
              padding: "12px 14px",
              color: "#fff",
            }}
          >
            <div style={{ fontSize: 13, fontWeight: 600, opacity: 0.9 }}>{g.label}</div>
            <div style={{ fontSize: 22, fontWeight: 700, marginTop: 4 }}>
              {formatGroupPct(g.allocation_pct)}
            </div>
            <div style={{ fontSize: 12, opacity: 0.9, marginTop: 2 }}>
              XIRR {formatXirr(g.xirr, g.xirr_reason)}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

function AllocationDriftSection({
  drift,
  loaded,
}: {
  drift: AllocationDrift | null;
  loaded: boolean;
}) {
  if (!loaded) {
    return (
      <div className="card">
        <h3>Allocation Drift</h3>
        <p className="muted">Loading…</p>
      </div>
    );
  }

  if (drift === null) {
    return (
      <div className="card">
        <h3>Allocation Drift</h3>
        <p className="muted">
          No risk profile set — target allocation isn't configured yet (there's no setup screen
          for this in the UI today; it's set via <code>POST /risk-profile</code>).
        </p>
      </div>
    );
  }

  return (
    <div className="card">
      <h3>Allocation Drift</h3>
      <p className="muted" style={{ marginBottom: 12 }}>
        Persona: {drift.persona}
      </p>
      <table>
        <thead>
          <tr>
            <th>Asset class</th>
            <th>Target</th>
            <th>Actual</th>
            <th>Drift</th>
          </tr>
        </thead>
        <tbody>
          <tr>
            <td>Equity</td>
            <td>{drift.target_equity_pct.toFixed(1)}%</td>
            <td>{formatGroupPct(drift.actual_equity_pct)}</td>
            <td
              className={
                drift.equity_drift_pct !== null
                  ? drift.equity_drift_pct >= 0
                    ? "positive"
                    : "negative"
                  : ""
              }
            >
              {formatDriftPct(drift.equity_drift_pct)}
            </td>
          </tr>
          <tr>
            <td>Debt</td>
            <td>{drift.target_debt_pct.toFixed(1)}%</td>
            <td>{formatGroupPct(drift.actual_debt_pct)}</td>
            <td
              className={
                drift.debt_drift_pct !== null
                  ? drift.debt_drift_pct >= 0
                    ? "positive"
                    : "negative"
                  : ""
              }
            >
              {formatDriftPct(drift.debt_drift_pct)}
            </td>
          </tr>
          <tr>
            <td>Other</td>
            <td>—</td>
            <td>{formatGroupPct(drift.actual_other_pct)}</td>
            <td>—</td>
          </tr>
        </tbody>
      </table>
      {drift.schemes_missing_metadata > 0 && (
        <p className="muted" style={{ marginTop: 12 }}>
          {drift.schemes_missing_metadata} scheme(s) missing category metadata, counted as
          "Other".
        </p>
      )}
    </div>
  );
}

export default function Dashboard() {
  const [apiStatus, setApiStatus] = useState<ApiStatus>("checking");
  const [metrics, setMetrics] = useState<PortfolioMetrics | null>(null);
  const [comparison, setComparison] = useState<PortfolioBenchmarkComparison | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [categoryGroups, setCategoryGroups] = useState<CategoryGroup[] | null>(null);
  const [subCategoryGroups, setSubCategoryGroups] = useState<CategoryGroup[] | null>(null);
  const [drift, setDrift] = useState<AllocationDrift | null>(null);
  const [driftLoaded, setDriftLoaded] = useState(false);

  useEffect(() => {
    getHealth()
      .then(() => setApiStatus("connected"))
      .catch(() => setApiStatus("unreachable"));

    getPortfolioMetrics()
      .then(setMetrics)
      .catch((err: unknown) => setError(err instanceof Error ? err.message : "Unknown error"));

    getPortfolioBenchmarkComparison()
      .then(setComparison)
      .catch((err: unknown) => setError(err instanceof Error ? err.message : "Unknown error"));

    getCategoryBreakdown("category")
      .then(setCategoryGroups)
      .catch(() => setCategoryGroups([]));

    getCategoryBreakdown("sub_category")
      .then(setSubCategoryGroups)
      .catch(() => setSubCategoryGroups([]));

    getAllocationDrift()
      .then((d) => {
        setDrift(d);
        setDriftLoaded(true);
      })
      .catch(() => setDriftLoaded(true));
  }, []);

  const alphaClass =
    comparison?.alpha_pct === null || comparison?.alpha_pct === undefined
      ? ""
      : comparison.alpha_pct >= 0
        ? "positive"
        : "negative";

  return (
    <div>
      <h1>Dashboard</h1>
      <p className="muted" style={{ marginBottom: 24 }}>
        API {apiStatus === "connected" ? "connected" : apiStatus}
      </p>

      {error && (
        <div className="card">
          <p>Failed to load portfolio summary: {error}</p>
        </div>
      )}

      {metrics && (
        <>
          <h2>Portfolio</h2>
          <div className="card-grid">
            <div className="stat">
              <div className="stat-label">Current value</div>
              <div className="stat-value">{formatPaise(metrics.current_value_paise)}</div>
            </div>
            <div className="stat">
              <div className="stat-label">Portfolio XIRR</div>
              <div className="stat-value">
                {metrics.xirr !== null ? formatPct(metrics.xirr) : "n/a"}
              </div>
              {metrics.xirr === null && <div className="muted">{metrics.xirr_reason}</div>}
            </div>
            {comparison && (
              <>
                <div className="stat">
                  <div className="stat-label">Benchmark XIRR</div>
                  <div className="stat-value">
                    {comparison.benchmark_xirr !== null
                      ? formatPct(comparison.benchmark_xirr)
                      : "n/a"}
                  </div>
                  <div className="muted">{comparison.benchmark_id}</div>
                </div>
                <div className="stat">
                  <div className="stat-label">Alpha</div>
                  <div className={`stat-value ${alphaClass}`}>
                    {formatAlpha(comparison.alpha_pct)}
                  </div>
                </div>
              </>
            )}
          </div>

          <div className="card">
            <h3>Details</h3>
            <table>
              <tbody>
                <tr>
                  <td>Invested</td>
                  <td>{formatPaise(metrics.invested_paise)}</td>
                </tr>
                <tr>
                  <td>Received (redemptions/dividends)</td>
                  <td>{formatPaise(metrics.received_paise)}</td>
                </tr>
                <tr>
                  <td>Positions missing NAV</td>
                  <td>{metrics.positions_missing_nav}</td>
                </tr>
              </tbody>
            </table>
            {comparison && <p className="muted" style={{ marginTop: 12 }}>{comparison.proxy_note}</p>}
          </div>

          <AssetAllocationSection groups={categoryGroups} />
          <CategoryAllocationSection groups={subCategoryGroups} />
          <AllocationDriftSection drift={drift} loaded={driftLoaded} />
        </>
      )}
    </div>
  );
}
