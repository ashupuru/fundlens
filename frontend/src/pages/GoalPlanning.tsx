import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import {
  createGoal,
  deleteGoal,
  getGoalHoldings,
  getGoalMonteCarlo,
  getGoalProjection,
  getGoals,
  getHoldings,
  setGoalHoldings,
  type Goal,
  type GoalHoldingRef,
  type GoalMonteCarloResult,
  type GoalProjection,
  type Holding,
} from "../api/client";

function formatPaise(paise: number): string {
  return `₹${(paise / 100).toLocaleString("en-IN", { maximumFractionDigits: 0 })}`;
}

function holdingKey(h: { folio: string; scheme_key: string }): string {
  return `${h.folio}::${h.scheme_key}`;
}

type ProjectionState = { status: "loading" } | { status: "no-risk-profile" } | { status: "ready"; projection: GoalProjection };

function ProjectionPanel({ state }: { state: ProjectionState }) {
  if (state.status === "loading") {
    return <p className="muted">Loading projection…</p>;
  }
  if (state.status === "no-risk-profile") {
    return (
      <p className="muted">
        No risk profile set — projections need one (set via <code>POST /risk-profile</code>).
      </p>
    );
  }
  const p = state.projection;
  return (
    <div>
      <table>
        <tbody>
          <tr>
            <td>Current tagged corpus</td>
            <td>{formatPaise(p.current_corpus_paise)}</td>
          </tr>
          <tr>
            <td>Target (inflation-adjusted)</td>
            <td>{formatPaise(p.inflated_target_paise)}</td>
          </tr>
          <tr>
            <td>Projected corpus at target date</td>
            <td>{formatPaise(p.projected_corpus_paise)}</td>
          </tr>
          <tr>
            <td>Required monthly SIP</td>
            <td>{formatPaise(p.required_monthly_sip_paise)}</td>
          </tr>
        </tbody>
      </table>
      {p.current_corpus_unavailable_paise > 0 && (
        <p className="muted" style={{ marginTop: 8 }}>
          {formatPaise(p.current_corpus_unavailable_paise)} of tagged holdings couldn't be priced
          (no NAV on file) and is excluded from the corpus above.
        </p>
      )}
      <p className="muted" style={{ marginTop: 8 }}>
        Blended expected return: {p.blended_expected_return_pct}% · {p.years_to_target.toFixed(1)}{" "}
        years to go
      </p>
      <h4 style={{ marginTop: 16, marginBottom: 6 }}>Sensitivity (required monthly SIP)</h4>
      <table>
        <thead>
          <tr>
            <th>Return assumption</th>
            <th>Required SIP</th>
          </tr>
        </thead>
        <tbody>
          {p.sensitivity.map((s) => (
            <tr key={s.return_pct}>
              <td>{s.return_pct}%</td>
              <td>{formatPaise(s.required_monthly_sip_paise)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function formatPct(value: number): string {
  return `${value.toFixed(1)}%`;
}

type MonteCarloState =
  | { status: "idle" }
  | { status: "running" }
  | { status: "no-risk-profile" }
  | { status: "ready"; result: GoalMonteCarloResult };

function MonteCarloPanel({
  state,
  onRun,
}: {
  state: MonteCarloState;
  onRun: () => void;
}) {
  return (
    <div style={{ marginTop: 16 }}>
      <button onClick={onRun} disabled={state.status === "running"}>
        {state.status === "running" ? "Running 5,000 simulations…" : "Run Monte Carlo simulation"}
      </button>
      {state.status === "no-risk-profile" && (
        <p className="muted" style={{ marginTop: 8 }}>
          No risk profile set — simulation needs one (set via <code>POST /risk-profile</code>).
        </p>
      )}
      {state.status === "ready" && (
        <table style={{ marginTop: 8 }}>
          <tbody>
            <tr>
              <td>Success probability (no further contribution)</td>
              <td>{formatPct(state.result.success_probability_no_contribution_pct)}</td>
            </tr>
            <tr>
              <td>Success probability (with required SIP)</td>
              <td>{formatPct(state.result.success_probability_with_required_sip_pct)}</td>
            </tr>
            <tr>
              <td>Outcome range (10th–90th percentile, with required SIP)</td>
              <td>
                {formatPaise(state.result.percentile_10_corpus_paise)} –{" "}
                {formatPaise(state.result.percentile_90_corpus_paise)}
              </td>
            </tr>
            <tr>
              <td>Median outcome</td>
              <td>{formatPaise(state.result.percentile_50_corpus_paise)}</td>
            </tr>
          </tbody>
        </table>
      )}
    </div>
  );
}

function GoalCard({
  goal,
  allHoldings,
  onDeleted,
}: {
  goal: Goal;
  allHoldings: Holding[];
  onDeleted: () => void;
}) {
  const [taggedKeys, setTaggedKeys] = useState<Set<string> | null>(null);
  const [projectionState, setProjectionState] = useState<ProjectionState>({ status: "loading" });
  const [showTagger, setShowTagger] = useState(false);
  const [monteCarloState, setMonteCarloState] = useState<MonteCarloState>({ status: "idle" });

  async function runMonteCarlo() {
    setMonteCarloState({ status: "running" });
    const result = await getGoalMonteCarlo(goal.id);
    setMonteCarloState(
      result === null ? { status: "no-risk-profile" } : { status: "ready", result },
    );
  }

  async function refreshProjection() {
    setProjectionState({ status: "loading" });
    const projection = await getGoalProjection(goal.id);
    setProjectionState(
      projection === null ? { status: "no-risk-profile" } : { status: "ready", projection },
    );
  }

  useEffect(() => {
    getGoalHoldings(goal.id).then((rows) => setTaggedKeys(new Set(rows.map(holdingKey))));
    void refreshProjection();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [goal.id]);

  async function toggleHolding(h: Holding) {
    if (taggedKeys === null) return;
    const key = holdingKey(h);
    const next = new Set(taggedKeys);
    if (next.has(key)) {
      next.delete(key);
    } else {
      next.add(key);
    }
    setTaggedKeys(next);
    const refs: GoalHoldingRef[] = allHoldings
      .filter((holding) => next.has(holdingKey(holding)))
      .map((holding) => ({ folio: holding.folio, scheme_key: holding.scheme_key }));
    await setGoalHoldings(goal.id, refs);
    await refreshProjection();
  }

  return (
    <div className="card">
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
        <h3>{goal.name}</h3>
        <button onClick={() => void deleteGoal(goal.id).then(onDeleted)}>Delete</button>
      </div>
      <p className="muted" style={{ marginBottom: 12 }}>
        Target {formatPaise(goal.target_amount_paise)} (today's rupees) by {goal.target_date}
      </p>

      <ProjectionPanel state={projectionState} />
      <MonteCarloPanel state={monteCarloState} onRun={() => void runMonteCarlo()} />

      <button style={{ marginTop: 12 }} onClick={() => setShowTagger((v) => !v)}>
        {showTagger ? "Hide holdings" : "Tag holdings"}
      </button>
      {showTagger && taggedKeys !== null && (
        <div style={{ marginTop: 8 }}>
          {allHoldings.length === 0 && <p className="muted">No current holdings to tag.</p>}
          {allHoldings.map((h) => (
            <label key={holdingKey(h)} style={{ display: "block", marginBottom: 4 }}>
              <input
                type="checkbox"
                checked={taggedKeys.has(holdingKey(h))}
                onChange={() => void toggleHolding(h)}
              />{" "}
              {h.scheme_name} ({h.folio})
            </label>
          ))}
        </div>
      )}
    </div>
  );
}

export default function GoalPlanning() {
  const [goals, setGoals] = useState<Goal[] | null>(null);
  const [allHoldings, setAllHoldings] = useState<Holding[]>([]);
  const [name, setName] = useState("");
  const [targetAmount, setTargetAmount] = useState("");
  const [targetDate, setTargetDate] = useState("");
  const [inflationOverride, setInflationOverride] = useState("");
  const [includeInRetirement, setIncludeInRetirement] = useState(true);
  const [createError, setCreateError] = useState<string | null>(null);

  function refreshGoals() {
    getGoals().then(setGoals);
  }

  useEffect(() => {
    refreshGoals();
    getHoldings().then(setAllHoldings);
  }, []);

  async function handleCreate(e: React.FormEvent) {
    e.preventDefault();
    setCreateError(null);
    try {
      await createGoal(
        name,
        targetAmount,
        targetDate,
        inflationOverride === "" ? null : inflationOverride,
        includeInRetirement,
      );
      setName("");
      setTargetAmount("");
      setTargetDate("");
      setInflationOverride("");
      setIncludeInRetirement(true);
      refreshGoals();
    } catch (err) {
      setCreateError(err instanceof Error ? err.message : "Unknown error");
    }
  }

  return (
    <div>
      <h1>Goal Planning</h1>
      <p className="muted" style={{ marginBottom: 20 }}>
        Tag holdings toward a savings goal to see the projected corpus and the monthly SIP needed
        to reach it, adjusted for inflation.
      </p>

      <div className="card">
        <h3>New goal</h3>
        <form onSubmit={(e) => void handleCreate(e)}>
          <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
            <input
              type="text"
              placeholder="Goal name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              required
            />
            <input
              type="number"
              placeholder="Target amount (₹, today's value)"
              value={targetAmount}
              onChange={(e) => setTargetAmount(e.target.value)}
              min="0"
              step="0.01"
              required
            />
            <input
              type="date"
              value={targetDate}
              onChange={(e) => setTargetDate(e.target.value)}
              required
            />
            <input
              type="number"
              placeholder="Inflation % (optional)"
              value={inflationOverride}
              onChange={(e) => setInflationOverride(e.target.value)}
              min="0"
              step="0.01"
              title="Leave blank to use the global inflation assumption. Education and healthcare typically run higher; enter 0 for a cost that shouldn't inflate."
            />
            <button type="submit">Add goal</button>
          </div>
          <label
            style={{ display: "flex", alignItems: "center", gap: 6, marginTop: 10, fontSize: 14 }}
          >
            <input
              type="checkbox"
              checked={includeInRetirement}
              onChange={(e) => setIncludeInRetirement(e.target.checked)}
            />
            Draw this from the portfolio in the retirement projection
          </label>
        </form>
        <p className="muted" style={{ marginTop: 8 }}>
          Included goals are withdrawn from the corpus in the{" "}
          <Link to="/retirement">retirement projection</Link>, inflated to their target year.
          Untick for a goal already counted elsewhere (a goal tracking retirement itself would
          double-count the monthly income) or funded from outside the portfolio.
        </p>
        {createError && <p style={{ color: "var(--negative)", marginTop: 8 }}>{createError}</p>}
      </div>

      {goals === null && (
        <div className="card">
          <p className="muted">Loading…</p>
        </div>
      )}
      {goals !== null && goals.length === 0 && (
        <div className="card">
          <p className="muted">No goals yet — add one above.</p>
        </div>
      )}
      {goals?.map((goal) => (
        <GoalCard key={goal.id} goal={goal} allHoldings={allHoldings} onDeleted={refreshGoals} />
      ))}
    </div>
  );
}
