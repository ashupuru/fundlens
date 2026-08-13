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

  return (
    <main>
      <h1>FundLens</h1>
      <p>API status: {apiStatus}</p>

      <h2>Portfolio</h2>
      {error && <p>Failed to load portfolio summary: {error}</p>}
      {metrics && (
        <table>
          <tbody>
            <tr>
              <td>Portfolio XIRR</td>
              <td>
                {metrics.xirr !== null ? formatPct(metrics.xirr) : `n/a (${metrics.xirr_reason})`}
              </td>
            </tr>
            {comparison && (
              <>
                <tr>
                  <td>Benchmark ({comparison.benchmark_id}) XIRR</td>
                  <td>
                    {comparison.benchmark_xirr !== null
                      ? formatPct(comparison.benchmark_xirr)
                      : `n/a (${comparison.benchmark_xirr_reason})`}
                  </td>
                </tr>
                <tr>
                  <td>Alpha</td>
                  <td>{formatAlpha(comparison.alpha_pct)}</td>
                </tr>
              </>
            )}
            <tr>
              <td>Positions missing NAV</td>
              <td>{metrics.positions_missing_nav}</td>
            </tr>
          </tbody>
        </table>
      )}
      {comparison && <p style={{ fontSize: 14 }}>{comparison.proxy_note}</p>}
    </main>
  );
}
