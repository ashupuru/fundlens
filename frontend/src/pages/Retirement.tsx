import { useEffect, useState } from "react";
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
import {
  getRetirementProfile,
  getRetirementProjection,
  getRetirementScenarios,
  setRetirementProfile,
  type RetirementProfile,
  type RetirementProjection,
  type RetirementScenario,
} from "../api/client";

const BRAND_COLOR = "#1c2b4a";
const ACCENT_COLOR = "#2f8f83";
const NEGATIVE_COLOR = "#b3402c";

const REASON_MESSAGES: Record<string, string> = {
  retirement_profile_incomplete:
    "Fill in the retirement plan below to see a projection — nothing is assumed on your behalf.",
  current_age_at_or_past_retirement_age:
    "Your current age is at or past your retirement age. Post-retirement-only projections aren't supported yet.",
  life_expectancy_not_after_retirement_age:
    "Life expectancy must be later than retirement age.",
};

const EXCLUDED_REASON_MESSAGES: Record<string, string> = {
  target_date_already_passed: "target date has passed",
  beyond_life_expectancy: "falls beyond life expectancy",
};

function formatPaise(paise: number): string {
  const rupees = paise / 100;
  if (Math.abs(rupees) >= 10_000_000) return `₹${(rupees / 10_000_000).toFixed(2)} Cr`;
  if (Math.abs(rupees) >= 100_000) return `₹${(rupees / 100_000).toFixed(2)} L`;
  return `₹${rupees.toLocaleString("en-IN", { maximumFractionDigits: 0 })}`;
}

function RetirementForm({
  initial,
  onSaved,
}: {
  initial: RetirementProfile | null;
  onSaved: () => void;
}) {
  const [currentAge, setCurrentAge] = useState(String(initial?.current_age ?? ""));
  const [retirementAge, setRetirementAge] = useState(String(initial?.retirement_age ?? ""));
  const [lifeExpectancy, setLifeExpectancy] = useState(String(initial?.life_expectancy ?? "85"));
  const [annualInvestment, setAnnualInvestment] = useState(
    initial?.annual_investment_paise != null ? String(initial.annual_investment_paise / 100) : "",
  );
  const [stepUp, setStepUp] = useState(initial?.annual_investment_step_up_pct ?? "0");
  const [monthlyWithdrawal, setMonthlyWithdrawal] = useState(
    initial?.monthly_withdrawal_today_paise != null
      ? String(initial.monthly_withdrawal_today_paise / 100)
      : "",
  );
  const [postReturn, setPostReturn] = useState(initial?.post_retirement_return_pct ?? "");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setSaving(true);
    setError(null);
    try {
      await setRetirementProfile({
        current_age: currentAge === "" ? null : Number(currentAge),
        retirement_age: retirementAge === "" ? null : Number(retirementAge),
        life_expectancy: lifeExpectancy === "" ? null : Number(lifeExpectancy),
        annual_investment_rupees: annualInvestment === "" ? null : annualInvestment,
        annual_investment_step_up_pct: stepUp === "" ? null : stepUp,
        monthly_withdrawal_today_rupees: monthlyWithdrawal === "" ? null : monthlyWithdrawal,
        post_retirement_return_pct: postReturn === "" ? null : postReturn,
      });
      onSaved();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unknown error");
    } finally {
      setSaving(false);
    }
  }

  const field = { display: "flex", flexDirection: "column" as const, gap: 4, flex: "1 1 160px" };

  return (
    <form onSubmit={(e) => void handleSubmit(e)}>
      <div style={{ display: "flex", flexWrap: "wrap", gap: 12, marginBottom: 12 }}>
        <label style={field}>
          Current age
          <input type="number" value={currentAge} onChange={(e) => setCurrentAge(e.target.value)} />
        </label>
        <label style={field}>
          Retirement age
          <input
            type="number"
            value={retirementAge}
            onChange={(e) => setRetirementAge(e.target.value)}
          />
        </label>
        <label style={field}>
          Life expectancy
          <input
            type="number"
            value={lifeExpectancy}
            onChange={(e) => setLifeExpectancy(e.target.value)}
          />
        </label>
      </div>
      <div style={{ display: "flex", flexWrap: "wrap", gap: 12, marginBottom: 12 }}>
        <label style={field}>
          Annual investment (₹)
          <input
            type="number"
            value={annualInvestment}
            onChange={(e) => setAnnualInvestment(e.target.value)}
          />
        </label>
        <label style={field}>
          Annual step-up (%)
          <input type="number" value={stepUp} onChange={(e) => setStepUp(e.target.value)} />
        </label>
        <label style={field}>
          Monthly need in today's ₹
          <input
            type="number"
            value={monthlyWithdrawal}
            onChange={(e) => setMonthlyWithdrawal(e.target.value)}
          />
        </label>
        <label style={field}>
          Post-retirement return (%)
          <input
            type="number"
            placeholder="same as pre-retirement"
            value={postReturn}
            onChange={(e) => setPostReturn(e.target.value)}
          />
        </label>
      </div>
      {error && <p style={{ color: "var(--negative)", marginBottom: 8 }}>{error}</p>}
      <button type="submit" disabled={saving}>
        {saving ? "Saving…" : "Save retirement plan"}
      </button>
    </form>
  );
}

function ReadinessSection({ p }: { p: RetirementProjection }) {
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

function EventsSection({ p }: { p: RetirementProjection }) {
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

function ScenarioSection({ scenarios }: { scenarios: RetirementScenario[] }) {
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

function CorpusChart({ p }: { p: RetirementProjection }) {
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

function YearTable({ p }: { p: RetirementProjection }) {
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

export default function Retirement() {
  const [profile, setProfile] = useState<RetirementProfile | null>(null);
  const [projection, setProjection] = useState<RetirementProjection | null>(null);
  const [scenarios, setScenarios] = useState<RetirementScenario[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function refresh() {
    Promise.all([getRetirementProfile(), getRetirementProjection(), getRetirementScenarios()])
      .then(([pr, pj, sc]) => {
        setProfile(pr);
        setProjection(pj);
        setScenarios(sc);
        setLoaded(true);
      })
      .catch((err: unknown) => {
        setError(err instanceof Error ? err.message : "Unknown error");
        setLoaded(true);
      });
  }

  useEffect(refresh, []);

  if (!loaded) {
    return (
      <div>
        <h1>Retirement</h1>
        <p className="muted">Loading…</p>
      </div>
    );
  }

  if (error) {
    return (
      <div>
        <h1>Retirement</h1>
        <div className="card">
          <p>{error}</p>
          <p className="muted" style={{ marginTop: 8 }}>
            A risk profile is required first — set one on the Dashboard, since the expected return
            is derived from your target allocation.
          </p>
        </div>
      </div>
    );
  }

  const hasProjection = projection !== null && projection.reason === null;

  return (
    <div>
      <h1>Retirement</h1>

      {projection?.reason && (
        <div className="card">
          <p>{REASON_MESSAGES[projection.reason] ?? projection.reason}</p>
        </div>
      )}

      {hasProjection && projection && (
        <>
          <ReadinessSection p={projection} />
          <ScenarioSection scenarios={scenarios} />
          <CorpusChart p={projection} />
          <EventsSection p={projection} />
          <YearTable p={projection} />
        </>
      )}

      <div className="card">
        <h3>Your retirement plan</h3>
        <RetirementForm initial={profile} onSaved={refresh} />
      </div>

      {hasProjection && projection && (
        <p className="muted">
          Projection based on assumptions — actual market returns and inflation will vary. Starting
          corpus {formatPaise(projection.starting_corpus_paise ?? 0)} (your current portfolio
          value), {projection.blended_return_pct}% pre-retirement return (from your target
          allocation), {projection.post_retirement_return_pct}% post-retirement,{" "}
          {projection.inflation_pct}% inflation. Returns are applied to each year's opening balance
          and contributions added at year end. Your goals are included as life-event withdrawals.
          Pre-tax.
        </p>
      )}
    </div>
  );
}
