import { useEffect, useState } from "react";
import { Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { Link, useParams } from "react-router-dom";
import { getFundDetail, type FundDetail } from "../api/client";

const ROLLING_WINDOW_LABELS: Record<number, string> = { 1: "1 year", 3: "3 years", 5: "5 years" };

function formatPaise(paise: number | null | undefined): string {
  if (paise === null || paise === undefined) return "—";
  return `₹${(paise / 100).toLocaleString("en-IN", { maximumFractionDigits: 0 })}`;
}

function formatXirr(value: number | null | undefined, reason?: string | null): string {
  if (value === null || value === undefined) return reason ? `n/a (${reason})` : "n/a";
  return `${value >= 0 ? "+" : ""}${(value * 100).toFixed(2)}%`;
}

function formatPct(value: string | null | undefined): string {
  if (value === null || value === undefined) return "—";
  const n = Number(value);
  return Number.isFinite(n) ? `${n.toFixed(2)}%` : "—";
}

function formatAum(value: string | null | undefined): string {
  if (value === null || value === undefined) return "—";
  const n = Number(value);
  return Number.isFinite(n) ? `₹${n.toLocaleString("en-IN", { maximumFractionDigits: 0 })} Cr` : "—";
}

function formatRating(value: number | null | undefined, max: number): string {
  if (value === null || value === undefined) return "—";
  return `${value}/${max}`;
}

function formatAlphaPp(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return `${value >= 0 ? "+" : ""}${value.toFixed(2)}pp`;
}

function formatConsistency(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return `${value.toFixed(0)}%`;
}

function formatRatio(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return value.toFixed(2);
}

function formatDrawdown(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return `${value.toFixed(1)}%`;
}

export default function FundDetail() {
  const { amfiCode } = useParams<{ amfiCode: string }>();
  const [detail, setDetail] = useState<FundDetail | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!amfiCode) return;
    setDetail(null);
    setError(null);
    getFundDetail(amfiCode)
      .then(setDetail)
      .catch((err: unknown) => setError(err instanceof Error ? err.message : "Unknown error"));
  }, [amfiCode]);

  if (error) {
    return (
      <div>
        <h1>Fund detail</h1>
        <div className="card">
          <p>Failed to load: {error}</p>
        </div>
      </div>
    );
  }

  if (detail === null) {
    return (
      <div>
        <h1>Fund detail</h1>
        <p className="muted">Loading…</p>
      </div>
    );
  }

  return (
    <div>
      <h1>{detail.scheme_name ?? detail.amfi_code}</h1>
      <p className="muted" style={{ marginBottom: 20 }}>
        {detail.fund_house ?? "Unknown AMC"}
        {detail.category && ` · ${detail.category}`}
        {detail.sub_category && ` · ${detail.sub_category}`}
        {detail.plan_type && ` · ${detail.plan_type}`}
      </p>

      {!detail.metadata_available && (
        <div className="card">
          <p className="muted">
            No Groww listing found for this fund — rating, AUM, and returns aren't available.
            {detail.holdings.length > 0 && " Your position below still reflects your own data."}
          </p>
        </div>
      )}

      {detail.metadata_available && (
        <div className="card">
          <h2>Fund overview</h2>
          <div className="card-grid" style={{ marginBottom: 0 }}>
            <div className="stat">
              <div className="stat-label">AUM</div>
              <div className="stat-value">{formatAum(detail.aum)}</div>
            </div>
            <div className="stat">
              <div className="stat-label">Groww rating</div>
              <div className="stat-value">{formatRating(detail.groww_rating, 5)}</div>
            </div>
            <div className="stat">
              <div className="stat-label">Risk rating</div>
              <div className="stat-value">{formatRating(detail.risk_rating, 6)}</div>
            </div>
            <div className="stat">
              <div className="stat-label">Expense ratio</div>
              <div className="stat-value">{formatPct(detail.expense_ratio)}</div>
            </div>
          </div>
          <div className="card-grid" style={{ marginTop: 16, marginBottom: 0 }}>
            <div className="stat">
              <div className="stat-label">1Y return</div>
              <div className="stat-value">{formatPct(detail.return_1y)}</div>
            </div>
            <div className="stat">
              <div className="stat-label">3Y return</div>
              <div className="stat-value">{formatPct(detail.return_3y)}</div>
            </div>
            <div className="stat">
              <div className="stat-label">5Y return</div>
              <div className="stat-value">{formatPct(detail.return_5y)}</div>
            </div>
            <div className="stat">
              <div className="stat-label">10Y return</div>
              <div className="stat-value">{formatPct(detail.return_10y)}</div>
            </div>
          </div>
          {(detail.fund_manager || detail.exit_load) && (
            <p className="muted" style={{ marginTop: 16 }}>
              {detail.fund_manager && `Fund manager: ${detail.fund_manager}`}
              {detail.fund_manager && detail.exit_load && " — "}
              {detail.exit_load && `Exit load: ${detail.exit_load}`}
            </p>
          )}
        </div>
      )}

      <div className="card">
        <h2>Your position</h2>
        {detail.your_position === null ? (
          <p className="muted">You don't currently hold this fund.</p>
        ) : (
          <>
            <div className="card-grid" style={{ marginBottom: 16 }}>
              <div className="stat">
                <div className="stat-label">Current value</div>
                <div className="stat-value">
                  {formatPaise(detail.your_position.current_value_paise)}
                </div>
              </div>
              <div className="stat">
                <div className="stat-label">Invested</div>
                <div className="stat-value">
                  {formatPaise(
                    detail.holdings.reduce((sum, h) => sum + h.cost_basis_paise, 0),
                  )}
                </div>
              </div>
              <div className="stat">
                <div className="stat-label">XIRR</div>
                <div className="stat-value">
                  {formatXirr(detail.your_position.xirr, detail.your_position.xirr_reason)}
                </div>
              </div>
            </div>
            <table>
              <thead>
                <tr>
                  <th>Folio</th>
                  <th>Units</th>
                  <th>Avg. cost/unit</th>
                  <th>Cost basis</th>
                </tr>
              </thead>
              <tbody>
                {detail.holdings.map((h) => (
                  <tr key={h.folio}>
                    <td>{h.folio}</td>
                    <td>{h.units_remaining}</td>
                    <td>{h.average_cost_per_unit}</td>
                    <td>{formatPaise(h.cost_basis_paise)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </>
        )}
      </div>

      <div className="card">
        <h2>Rolling returns</h2>
        {detail.rolling_returns.length === 0 ? (
          <p className="muted">
            Not enough data to compute rolling returns — needs scheme category metadata and NAV
            history.
          </p>
        ) : (
          <>
            <p className="muted" style={{ marginBottom: 12 }}>
              Fund CAGR vs category benchmark across monthly-sampled trailing windows. 1-year is
              informational only — the hold/sell verdict is based on 3yr and 5yr consistency only.
            </p>
            <table style={{ marginBottom: 16 }}>
              <thead>
                <tr>
                  <th>Window</th>
                  <th>Mean alpha</th>
                  <th>Consistency</th>
                  <th>Samples</th>
                </tr>
              </thead>
              <tbody>
                {detail.rolling_returns.map((w) => (
                  <tr key={w.years}>
                    <td>{ROLLING_WINDOW_LABELS[w.years] ?? `${w.years}yr`}</td>
                    <td>
                      {w.mean_alpha_pct !== null
                        ? formatAlphaPp(w.mean_alpha_pct)
                        : `— (${w.reason ?? "unavailable"})`}
                    </td>
                    <td>{formatConsistency(w.consistency_pct)}</td>
                    <td>{w.sample_count}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {(() => {
              const threeYear = detail.rolling_returns.find((w) => w.years === 3);
              const chartData = (threeYear?.samples ?? []).filter((s) => s.alpha_pct !== null);
              if (chartData.length < 2) return null;
              return (
                <div>
                  <p className="muted" style={{ marginBottom: 8 }}>
                    3-year rolling alpha over time
                  </p>
                  <div style={{ width: "100%", height: 220 }}>
                    <ResponsiveContainer>
                      <LineChart data={chartData}>
                        <XAxis dataKey="as_of_date" tick={{ fontSize: 11 }} minTickGap={40} />
                        <YAxis
                          tickFormatter={(v: number) => `${v.toFixed(0)}pp`}
                          tick={{ fontSize: 11 }}
                          width={50}
                        />
                        <Tooltip formatter={(value) => formatAlphaPp(Number(value))} />
                        <Line
                          type="monotone"
                          dataKey="alpha_pct"
                          name="Alpha"
                          stroke="var(--brand)"
                          dot={false}
                          strokeWidth={2}
                        />
                      </LineChart>
                    </ResponsiveContainer>
                  </div>
                </div>
              );
            })()}
          </>
        )}
      </div>

      <div className="card">
        <h2>Risk-adjusted metrics</h2>
        {detail.risk_metrics.length === 0 ? (
          <p className="muted">
            Not enough data to compute risk-adjusted metrics — needs scheme category metadata and
            NAV history.
          </p>
        ) : (
          <>
            <p className="muted" style={{ marginBottom: 12 }}>
              Fund vs category benchmark (not literal peer funds — this app doesn't have NAV
              history for funds you don't hold).
            </p>
            <table>
              <thead>
                <tr>
                  <th>Window</th>
                  <th>Sharpe (fund)</th>
                  <th>Sharpe (benchmark)</th>
                  <th>Sortino (fund)</th>
                  <th>Sortino (benchmark)</th>
                  <th>Max drawdown (fund)</th>
                  <th>Max drawdown (benchmark)</th>
                </tr>
              </thead>
              <tbody>
                {detail.risk_metrics.map((w) => (
                  <tr key={w.years}>
                    <td>{ROLLING_WINDOW_LABELS[w.years] ?? `${w.years}yr`}</td>
                    {w.fund === null ? (
                      <td colSpan={6} className="muted">
                        {w.reason ?? "unavailable"}
                      </td>
                    ) : (
                      <>
                        <td>{formatRatio(w.fund.sharpe)}</td>
                        <td>{formatRatio(w.benchmark?.sharpe)}</td>
                        <td>{formatRatio(w.fund.sortino)}</td>
                        <td>{formatRatio(w.benchmark?.sortino)}</td>
                        <td>{formatDrawdown(w.fund.max_drawdown_pct)}</td>
                        <td>{formatDrawdown(w.benchmark?.max_drawdown_pct)}</td>
                      </>
                    )}
                  </tr>
                ))}
              </tbody>
            </table>
          </>
        )}
      </div>

      <div className="card">
        <h2>Similar funds ({detail.sub_category ?? "—"})</h2>
        {detail.similar_funds.length === 0 ? (
          <p className="muted">No similar funds found.</p>
        ) : (
          <table>
            <thead>
              <tr>
                <th>Fund</th>
                <th>Rating</th>
                <th>AUM</th>
                <th>3Y return</th>
              </tr>
            </thead>
            <tbody>
              {detail.similar_funds.map((f) => (
                <tr key={f.amfi_code}>
                  <td>
                    <Link to={`/funds/${encodeURIComponent(f.amfi_code)}`}>{f.scheme_name}</Link>
                  </td>
                  <td>{formatRating(f.groww_rating, 5)}</td>
                  <td>{formatAum(f.aum)}</td>
                  <td>{formatPct(f.return_3y)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
