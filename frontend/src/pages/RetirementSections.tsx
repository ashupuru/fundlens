/** Display-only sections of the Retirement page: the readiness summary,
 * planned life events, the corpus chart, and the year-by-year table.
 */
import { useState } from "react";
import {
  CartesianGrid,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { RetirementProjection } from "../api/client";
import {
  ACCENT_COLOR,
  BRAND_COLOR,
  EXCLUDED_REASON_MESSAGES,
  NEGATIVE_COLOR,
  formatPaise,
} from "./retirementFormat";

export function ReadinessSection({ p }: { p: RetirementProjection }) {
  const readiness = p.readiness_pct;
  const onTrack = (p.surplus_or_shortfall_paise ?? 0) >= 0;
  return (
    <>
      <div className="card-grid">
        <div className="stat">
          <div className="stat-label">Projected corpus at {p.retirement_age}</div>
          <div className="stat-value">{formatPaise(p.corpus_at_retirement_paise ?? 0)}</div>
        </div>
        <div className="stat">
          <div className="stat-label">Required corpus</div>
          <div className="stat-value">
            {formatPaise(p.required_corpus_at_retirement_paise ?? 0)}
          </div>
        </div>
        <div className="stat">
          <div className="stat-label">{onTrack ? "Surplus" : "Shortfall"}</div>
          <div className={`stat-value ${onTrack ? "positive" : "negative"}`}>
            {formatPaise(Math.abs(p.surplus_or_shortfall_paise ?? 0))}
          </div>
        </div>
        <div className="stat">
          <div className="stat-label">Retirement readiness</div>
          <div className={`stat-value ${onTrack ? "positive" : "negative"}`}>
            {readiness !== null && readiness !== undefined ? `${readiness.toFixed(0)}%` : "—"}
          </div>
        </div>
      </div>

      {p.required_annual_investment_paise !== null &&
        p.required_annual_investment_paise !== undefined && (
          <div className="card">
            <h3>What it takes to get there</h3>
            <table>
              <tbody>
                <tr>
                  <td>Required investment, first year</td>
                  <td>
                    {formatPaise(p.required_annual_investment_paise)}/yr
                    <span className="muted">
                      {" "}
                      ({formatPaise(p.required_monthly_investment_paise ?? 0)}/month)
                    </span>
                  </td>
                </tr>
                <tr>
                  <td>You currently plan to invest</td>
                  <td>{formatPaise(p.current_annual_investment_paise ?? 0)}/yr</td>
                </tr>
                <tr>
                  <td>Gap</td>
                  <td
                    className={
                      (p.additional_annual_investment_paise ?? 0) > 0 ? "negative" : "positive"
                    }
                  >
                    {(p.additional_annual_investment_paise ?? 0) > 0
                      ? `${formatPaise(p.additional_annual_investment_paise ?? 0)}/yr more needed`
                      : "None — your current plan reaches the target"}
                  </td>
                </tr>
              </tbody>
            </table>
            <p className="muted" style={{ marginTop: 8 }}>
              The first-year figure; it steps up with your annual increase each year after.
            </p>
          </div>
        )}

      <div className="card">
        <h3>Retirement income</h3>
        <table>
          <tbody>
            <tr>
              <td>Monthly need in today's money</td>
              <td>{formatPaise(p.monthly_withdrawal_today_paise ?? 0)}</td>
            </tr>
            <tr>
              <td>
                Equivalent at age {p.retirement_age}
                <span className="muted"> (inflated {p.inflation_pct}%/yr)</span>
              </td>
              <td>{formatPaise(p.first_year_monthly_withdrawal_paise ?? 0)}</td>
            </tr>
            <tr>
              <td>Corpus lasts until</td>
              <td className={p.depletion_age !== null ? "negative" : "positive"}>
                {p.depletion_age !== null
                  ? `Age ${p.depletion_age} — depletes before ${p.life_expectancy}`
                  : `Age ${p.life_expectancy} (sustainable)`}
              </td>
            </tr>
            <tr>
              <td>Corpus at age {p.life_expectancy}</td>
              <td>{formatPaise(p.terminal_corpus_paise ?? 0)}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </>
  );
}

export function EventsSection({ p }: { p: RetirementProjection }) {
  if (p.events.length === 0) {
    return (
      <div className="card">
        <h3>Planned life events</h3>
        <p className="muted">
          No goals yet. Goals you create on the Goals page are treated as future claims on this
          same portfolio and withdrawn from the projection in the year they fall due.
        </p>
      </div>
    );
  }

  const included = p.events.filter((e) => e.excluded_reason === null);
  const excluded = p.events.filter((e) => e.excluded_reason !== null);

  return (
    <div className="card">
      <h3>Planned life events</h3>
      <p className="muted" style={{ marginBottom: 12 }}>
        Every goal is treated as a future claim on this portfolio — tagging holdings to a goal
        labels them, it doesn't set the money aside, and this projection starts from your whole
        portfolio.
      </p>
      {included.length > 0 && (
        <table>
          <thead>
            <tr>
              <th>Event</th>
              <th>Age</th>
              <th>Cost today</th>
              <th>Inflation</th>
              <th>Cost then</th>
            </tr>
          </thead>
          <tbody>
            {included.map((e) => (
              <tr key={e.goal_id}>
                <td>{e.name}</td>
                <td>{e.age}</td>
                <td>{formatPaise(e.today_cost_paise)}</td>
                <td>{e.inflation_pct_used}%</td>
                <td>{formatPaise(e.inflated_cost_paise ?? 0)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {(p.total_lump_sum_shortfall_paise ?? 0) > 0 && (
        <p style={{ color: "var(--negative)", marginTop: 12 }}>
          {formatPaise(p.total_lump_sum_shortfall_paise ?? 0)} of planned events can't be funded
          from the projected corpus.
        </p>
      )}
      {excluded.length > 0 && (
        <p className="muted" style={{ marginTop: 12 }}>
          Not included:{" "}
          {excluded
            .map(
              (e) =>
                `${e.name} (${EXCLUDED_REASON_MESSAGES[e.excluded_reason ?? ""] ?? e.excluded_reason})`,
            )
            .join(", ")}
          .
        </p>
      )}
    </div>
  );
}

export function CorpusChart({ p }: { p: RetirementProjection }) {
  const data = p.years.map((y) => ({
    age: y.age,
    corpus: y.closing_corpus_paise / 100,
  }));
  return (
    <div className="card">
      <h3>Corpus over time</h3>
      <div style={{ width: "100%", height: 280 }}>
        <ResponsiveContainer>
          <LineChart data={data} margin={{ left: 12, right: 12, top: 8 }}>
            <CartesianGrid strokeDasharray="3 3" opacity={0.3} />
            <XAxis dataKey="age" tick={{ fontSize: 11 }} />
            <YAxis
              tick={{ fontSize: 11 }}
              tickFormatter={(v: number) => `${(v / 10_000_000).toFixed(1)}Cr`}
            />
            <Tooltip
              formatter={(value) => formatPaise(Number(value) * 100)}
              labelFormatter={(age) => `Age ${age}`}
            />
            {p.retirement_age !== null && (
              <ReferenceLine
                x={p.retirement_age}
                stroke={ACCENT_COLOR}
                strokeDasharray="4 4"
                label={{ value: "Retires", position: "top", fontSize: 11 }}
              />
            )}
            <Line
              type="monotone"
              dataKey="corpus"
              stroke={p.depletion_age !== null ? NEGATIVE_COLOR : BRAND_COLOR}
              strokeWidth={2}
              dot={false}
            />
          </LineChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}

export function YearTable({ p }: { p: RetirementProjection }) {
  const [expanded, setExpanded] = useState(false);
  const rows = expanded ? p.years : p.years.slice(0, 10);
  return (
    <div className="card">
      <h3>Year-by-year projection</h3>
      <table>
        <thead>
          <tr>
            <th>Age</th>
            <th>Opening</th>
            <th>Investment</th>
            <th>Return</th>
            <th>Withdrawal</th>
            <th>Life events</th>
            <th>Closing</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((y) => (
            <tr key={y.age}>
              <td>
                {y.age}
                {y.phase === "retirement" && <span className="muted"> (retired)</span>}
              </td>
              <td>{formatPaise(y.opening_corpus_paise)}</td>
              <td>{y.investment_paise > 0 ? formatPaise(y.investment_paise) : "—"}</td>
              <td>{formatPaise(y.return_paise)}</td>
              <td className={y.withdrawal_paise > 0 ? "negative" : ""}>
                {y.withdrawal_paise > 0 ? formatPaise(y.withdrawal_paise) : "—"}
              </td>
              <td className={y.lump_sum_paise > 0 ? "negative" : ""}>
                {y.lump_sum_paise > 0 ? formatPaise(y.lump_sum_paise) : "—"}
                {y.lump_sum_shortfall_paise > 0 && (
                  <span className="muted">
                    {" "}
                    ({formatPaise(y.lump_sum_shortfall_paise)} unfunded)
                  </span>
                )}
              </td>
              <td>{formatPaise(y.closing_corpus_paise)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {p.years.length > 10 && (
        <button style={{ marginTop: 12 }} onClick={() => setExpanded((v) => !v)}>
          {expanded ? "Show less" : `Show all ${p.years.length} years`}
        </button>
      )}
    </div>
  );
}
