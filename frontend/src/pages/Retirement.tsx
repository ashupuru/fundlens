import { useEffect, useState } from "react";
import {
  getRetirementProfile,
  getRetirementProjection,
  getRetirementScenarios,
  setRetirementProfile,
  type RetirementProfile,
  type RetirementProjection,
  type RetirementScenario,
  type RetirementWhatIf,
} from "../api/client";
import { ScenarioSection, WhatIfSection } from "./RetirementPlanning";
import { REASON_MESSAGES, formatPaise } from "./retirementFormat";
import { CorpusChart, EventsSection, ReadinessSection, YearTable } from "./RetirementSections";

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

export default function Retirement() {
  const [profile, setProfile] = useState<RetirementProfile | null>(null);
  const [projection, setProjection] = useState<RetirementProjection | null>(null);
  const [scenarios, setScenarios] = useState<RetirementScenario[]>([]);
  const [whatIf, setWhatIf] = useState<RetirementWhatIf>({});
  const [whatIfProjection, setWhatIfProjection] = useState<RetirementProjection | null>(null);
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

  const whatIfActive = Object.values(whatIf).some(
    (v) => v !== null && v !== undefined && v !== "",
  );

  useEffect(() => {
    if (!whatIfActive) {
      setWhatIfProjection(null);
      return;
    }
    let cancelled = false;
    getRetirementProjection(whatIf)
      .then((p) => {
        if (!cancelled) setWhatIfProjection(p);
      })
      .catch(() => {
        if (!cancelled) setWhatIfProjection(null);
      });
    return () => {
      cancelled = true;
    };
  }, [whatIf, whatIfActive]);

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
          <WhatIfSection
            whatIf={whatIf}
            onChange={setWhatIf}
            onReset={() => setWhatIf({})}
            active={whatIfActive && whatIfProjection !== null}
            base={projection}
            current={whatIfProjection ?? projection}
          />
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
