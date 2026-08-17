import { useEffect, useState } from "react";
import {
  createGoal,
  deleteGoal,
  getGoalHoldings,
  getGoalProjection,
  getGoals,
  getHoldings,
  setGoalHoldings,
  type Goal,
  type GoalHoldingRef,
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
      await createGoal(name, targetAmount, targetDate);
      setName("");
      setTargetAmount("");
      setTargetDate("");
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
            <button type="submit">Add goal</button>
          </div>
        </form>
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
