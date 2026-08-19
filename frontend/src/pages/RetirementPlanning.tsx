/** The interactive planning sections: what-if exploration and the
 * conservative/base/optimistic scenario comparison.
 */
import type {
  RetirementProjection,
  RetirementScenario,
  RetirementWhatIf,
} from "../api/client";
import { formatPaise } from "./retirementFormat";

export function WhatIfSection({
  whatIf,
  onChange,
  onReset,
  active,
  base,
  current,
}: {
  whatIf: RetirementWhatIf;
  onChange: (next: RetirementWhatIf) => void;
  onReset: () => void;
  active: boolean;
  base: RetirementProjection;
  current: RetirementProjection;
}) {
  const field = { display: "flex", flexDirection: "column" as const, gap: 4, flex: "1 1 150px" };
  const delta = (a: number | null | undefined, b: number | null | undefined) =>
    (a ?? 0) - (b ?? 0);
  const corpusDelta = delta(
    current.corpus_at_retirement_paise,
    base.corpus_at_retirement_paise,
  );

  return (
    <div className="card">
      <h3>What if…</h3>
      <p className="muted" style={{ marginBottom: 12 }}>
        Try changes without saving them — your plan stays exactly as it is.
      </p>
      <div style={{ display: "flex", flexWrap: "wrap", gap: 12, marginBottom: 12 }}>
        <label style={field}>
          Retire at
          <input
            type="number"
            placeholder={String(base.retirement_age ?? "")}
            value={whatIf.retirement_age ?? ""}
            onChange={(e) =>
              onChange({
                ...whatIf,
                retirement_age: e.target.value === "" ? null : Number(e.target.value),
              })
            }
          />
        </label>
        <label style={field}>
          Annual investment (₹)
          <input
            type="number"
            placeholder={String((base.current_annual_investment_paise ?? 0) / 100)}
            value={whatIf.annual_investment_rupees ?? ""}
            onChange={(e) =>
              onChange({ ...whatIf, annual_investment_rupees: e.target.value || null })
            }
          />
        </label>
        <label style={field}>
          Monthly need today (₹)
          <input
            type="number"
            placeholder={String((base.monthly_withdrawal_today_paise ?? 0) / 100)}
            value={whatIf.monthly_withdrawal_today_rupees ?? ""}
            onChange={(e) =>
              onChange({ ...whatIf, monthly_withdrawal_today_rupees: e.target.value || null })
            }
          />
        </label>
        <label style={field}>
          Return shift (%)
          <input
            type="number"
            step="0.5"
            placeholder="0"
            value={whatIf.return_delta_pct ?? ""}
            onChange={(e) => onChange({ ...whatIf, return_delta_pct: e.target.value || null })}
          />
        </label>
      </div>

      {active && (
        <>
          <table>
            <thead>
              <tr>
                <th></th>
                <th>Your plan</th>
                <th>What if</th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <td>Corpus at retirement</td>
                <td>{formatPaise(base.corpus_at_retirement_paise ?? 0)}</td>
                <td className={corpusDelta >= 0 ? "positive" : "negative"}>
                  {formatPaise(current.corpus_at_retirement_paise ?? 0)}
                  {corpusDelta !== 0 && (
                    <span className="muted">
                      {" "}
                      ({corpusDelta > 0 ? "+" : "−"}
                      {formatPaise(Math.abs(corpusDelta))})
                    </span>
                  )}
                </td>
              </tr>
              <tr>
                <td>Readiness</td>
                <td>{(base.readiness_pct ?? 0).toFixed(0)}%</td>
                <td
                  className={
                    (current.readiness_pct ?? 0) >= (base.readiness_pct ?? 0)
                      ? "positive"
                      : "negative"
                  }
                >
                  {(current.readiness_pct ?? 0).toFixed(0)}%
                </td>
              </tr>
              <tr>
                <td>Corpus lasts until</td>
                <td>{base.depletion_age ?? base.life_expectancy}</td>
                <td className={current.depletion_age !== null ? "negative" : "positive"}>
                  {current.depletion_age ?? current.life_expectancy}
                </td>
              </tr>
              <tr>
                <td>Required investment</td>
                <td>{formatPaise(base.required_annual_investment_paise ?? 0)}/yr</td>
                <td>{formatPaise(current.required_annual_investment_paise ?? 0)}/yr</td>
              </tr>
            </tbody>
          </table>
          <button style={{ marginTop: 12 }} onClick={onReset}>
            Reset to my plan
          </button>
        </>
      )}
    </div>
  );
}

export function ScenarioSection({ scenarios }: { scenarios: RetirementScenario[] }) {
  const usable = scenarios.filter((s) => s.projection.reason === null);
  if (usable.length === 0) return null;

  return (
    <div className="card">
      <h3>Scenarios</h3>
      <p className="muted" style={{ marginBottom: 12 }}>
        The same plan under three sets of assumptions. The point isn't which number is right —
        it's how much the outcome moves when the assumptions do.
      </p>
      <table>
        <thead>
          <tr>
            <th>Scenario</th>
            <th>Return</th>
            <th>Inflation</th>
            <th>Corpus at retirement</th>
            <th>Required</th>
            <th>Readiness</th>
            <th>Outcome</th>
          </tr>
        </thead>
        <tbody>
          {usable.map((s) => {
            const p = s.projection;
            const depletes = p.depletion_age !== null;
            return (
              <tr key={s.key}>
                <td>
                  <strong>{s.label}</strong>
                </td>
                <td>{p.blended_return_pct}%</td>
                <td>{p.inflation_pct}%</td>
                <td>{formatPaise(p.corpus_at_retirement_paise ?? 0)}</td>
                <td>{formatPaise(p.required_corpus_at_retirement_paise ?? 0)}</td>
                <td className={(p.readiness_pct ?? 0) >= 100 ? "positive" : "negative"}>
                  {p.readiness_pct !== null && p.readiness_pct !== undefined
                    ? `${p.readiness_pct.toFixed(0)}%`
                    : "—"}
                </td>
                <td className={depletes ? "negative" : "positive"}>
                  {depletes
                    ? `Runs out at ${p.depletion_age}`
                    : `Lasts to ${p.life_expectancy} · ${formatPaise(p.terminal_corpus_paise ?? 0)} left`}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
