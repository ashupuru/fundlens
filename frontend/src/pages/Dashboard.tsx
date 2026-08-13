import { useEffect, useState } from "react";
import {
  getHealth,
  getPortfolioBenchmarkComparison,
  getPortfolioMetrics,
} from "../api/client";
import type { components } from "../api/schema";

type ApiStatus = "checking" | "connected" | "unreachable";
type PortfolioMetrics = components["schemas"]["PortfolioMetricsResponse"];
type PortfolioBenchmarkComparison = components["schemas"]["PortfolioBenchmarkComparisonResponse"];

function formatPct(value: number | null): string {
  if (value === null) return "—";
  return `${(value * 100).toFixed(2)}%`;
}

function formatAlpha(value: number | null): string {
  if (value === null) return "—";
  const sign = value >= 0 ? "+" : "";
  return `${sign}${value.toFixed(2)}%`;
}

function formatPaise(paise: number): string {
  return `₹${(paise / 100).toLocaleString("en-IN", { maximumFractionDigits: 0 })}`;
}

export default function Dashboard() {
  const [apiStatus, setApiStatus] = useState<ApiStatus>("checking");
  const [metrics, setMetrics] = useState<PortfolioMetrics | null>(null);
  const [comparison, setComparison] = useState<PortfolioBenchmarkComparison | null>(null);
  const [error, setError] = useState<string | null>(null);

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
        </>
      )}
    </div>
  );
}
