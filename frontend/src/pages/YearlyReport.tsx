import { useState } from "react";
import {
  capitalGainsCsvUrl,
  getYearlyReport,
  yearlyReportPdfUrl,
  type YearlyReport,
} from "../api/client";

function formatPaise(paise: number): string {
  return `₹${(paise / 100).toLocaleString("en-IN", { maximumFractionDigits: 0 })}`;
}

function formatXirr(rate: number | null, reason: string | null): string {
  if (rate === null) return reason ? `n/a (${reason})` : "n/a";
  return `${(rate * 100).toFixed(2)}%`;
}

function formatAlpha(alphaPct: number | null): string {
  if (alphaPct === null) return "—";
  const sign = alphaPct >= 0 ? "+" : "";
  return `${sign}${alphaPct.toFixed(2)}%`;
}

function currentFyLabel(): string {
  const now = new Date();
  const year = now.getMonth() >= 3 ? now.getFullYear() : now.getFullYear() - 1;
  return `${year}-${String((year + 1) % 100).padStart(2, "0")}`;
}

type LoadState =
  | { status: "idle" }
  | { status: "loading" }
  | { status: "error"; message: string }
  | { status: "ready"; report: YearlyReport };

export default function YearlyReportPage() {
  const [fy, setFy] = useState(currentFyLabel());
  const [state, setState] = useState<LoadState>({ status: "idle" });

  async function handleView() {
    setState({ status: "loading" });
    try {
      const report = await getYearlyReport(fy);
      setState({ status: "ready", report });
    } catch (err) {
      setState({ status: "error", message: err instanceof Error ? err.message : "Unknown error" });
    }
  }

  const report = state.status === "ready" ? state.report : null;

  return (
    <div>
      <h1>Yearly Report</h1>
      <p className="muted" style={{ marginBottom: 20 }}>
        FY-aligned (Apr–Mar) summary: opening/closing value, XIRR, benchmark alpha, best/worst
        funds, allocation drift, estimated fees, and realized gains.
      </p>

      <div className="card">
        <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
          <input
            type="text"
            value={fy}
            onChange={(e) => setFy(e.target.value)}
            placeholder="2024-25"
            style={{ width: 100 }}
          />
          <button onClick={() => void handleView()} disabled={state.status === "loading"}>
            {state.status === "loading" ? "Loading…" : "View"}
          </button>
          <a href={yearlyReportPdfUrl(fy)} target="_blank" rel="noreferrer">
            Download PDF
          </a>
          <a href={capitalGainsCsvUrl(fy)} target="_blank" rel="noreferrer">
            Download capital gains CSV
          </a>
        </div>
        {state.status === "error" && (
          <p style={{ color: "var(--negative)", marginTop: 8 }}>{state.message}</p>
        )}
      </div>

      {report && (
        <>
          <div className="card">
            <h3>Portfolio Value</h3>
            <p className="muted" style={{ marginBottom: 8 }}>
              {report.fy_start} to {report.report_end}
            </p>
            <table>
              <tbody>
                <tr>
                  <td>Opening value</td>
                  <td>{formatPaise(report.opening_value_paise)}</td>
                </tr>
                <tr>
                  <td>Closing value</td>
                  <td>{formatPaise(report.closing_value_paise)}</td>
                </tr>
              </tbody>
            </table>
          </div>

          <div className="card">
            <h3>Returns</h3>
            <table>
              <tbody>
                <tr>
                  <td>This year's XIRR</td>
                  <td>
                    {formatXirr(report.portfolio_xirr_this_year, report.portfolio_xirr_this_year_reason)}
                  </td>
                </tr>
                <tr>
                  <td>Since-inception XIRR</td>
                  <td>
                    {formatXirr(
                      report.portfolio_xirr_since_inception,
                      report.portfolio_xirr_since_inception_reason,
                    )}
                  </td>
                </tr>
                <tr>
                  <td>Benchmark ({report.benchmark_id}) since inception</td>
                  <td>
                    {formatXirr(
                      report.benchmark_xirr_since_inception,
                      report.benchmark_xirr_since_inception_reason,
                    )}
                  </td>
                </tr>
                <tr>
                  <td>Alpha since inception</td>
                  <td>{formatAlpha(report.alpha_pct_since_inception)}</td>
                </tr>
              </tbody>
            </table>
          </div>

          <div className="card">
            <h3>Best / Worst Funds This Year</h3>
            {report.best_funds.length === 0 ? (
              <p className="muted">Not enough distinct positions this year to rank.</p>
            ) : (
              <div style={{ display: "flex", gap: 24, flexWrap: "wrap" }}>
                <table style={{ flex: 1 }}>
                  <thead>
                    <tr>
                      <th>Best</th>
                      <th>XIRR</th>
                    </tr>
                  </thead>
                  <tbody>
                    {report.best_funds.map((f) => (
                      <tr key={f.scheme_key}>
                        <td>{f.scheme_name}</td>
                        <td>{formatXirr(f.xirr, f.xirr_reason)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                <table style={{ flex: 1 }}>
                  <thead>
                    <tr>
                      <th>Worst</th>
                      <th>XIRR</th>
                    </tr>
                  </thead>
                  <tbody>
                    {report.worst_funds.map((f) => (
                      <tr key={f.scheme_key}>
                        <td>{f.scheme_name}</td>
                        <td>{formatXirr(f.xirr, f.xirr_reason)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>

          <div className="card">
            <h3>Allocation Drift</h3>
            {report.allocation_drift ? (
              <table>
                <thead>
                  <tr>
                    <th></th>
                    <th>Target</th>
                    <th>Actual</th>
                  </tr>
                </thead>
                <tbody>
                  <tr>
                    <td>Equity</td>
                    <td>{report.allocation_drift.target_equity_pct.toFixed(1)}%</td>
                    <td>
                      {report.allocation_drift.actual_equity_pct === null
                        ? "—"
                        : `${report.allocation_drift.actual_equity_pct.toFixed(1)}%`}
                    </td>
                  </tr>
                  <tr>
                    <td>Debt</td>
                    <td>{report.allocation_drift.target_debt_pct.toFixed(1)}%</td>
                    <td>
                      {report.allocation_drift.actual_debt_pct === null
                        ? "—"
                        : `${report.allocation_drift.actual_debt_pct.toFixed(1)}%`}
                    </td>
                  </tr>
                </tbody>
              </table>
            ) : (
              <p className="muted">No risk profile set — target allocation not configured.</p>
            )}
          </div>

          <div className="card">
            <h3>Estimated Annual Fees</h3>
            <p>
              {formatPaise(report.estimated_annual_fee_paise)} / year across currently held funds
              {report.fee_funds_excluded > 0 &&
                ` (${report.fee_funds_excluded} fund(s) excluded — expense ratio or NAV unavailable)`}
            </p>
          </div>

          <div className="card">
            <h3>Realized Gains</h3>
            <table>
              <tbody>
                <tr>
                  <td>Total STCG</td>
                  <td>{formatPaise(report.capital_gains.total_stcg_paise)}</td>
                </tr>
                <tr>
                  <td>Total LTCG</td>
                  <td>{formatPaise(report.capital_gains.total_ltcg_paise)}</td>
                </tr>
                <tr>
                  <td>Total taxable gain</td>
                  <td>{formatPaise(report.capital_gains.total_taxable_gain_paise)}</td>
                </tr>
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  );
}
