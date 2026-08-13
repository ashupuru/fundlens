import { useEffect, useState } from "react";
import {
  getBenchmarkComparisons,
  getDirectVsRegularAudit,
  getVerdicts,
} from "../api/client";
import type { components } from "../api/schema";

type Verdict = components["schemas"]["VerdictResponse"];
type BenchmarkComparison = components["schemas"]["BenchmarkComparisonResponse"];

type FundRow = {
  folio: string;
  schemeName: string;
  verdict: Verdict | undefined;
  comparison: BenchmarkComparison | undefined;
  planType: string | undefined;
};

function key(folio: string, schemeName: string): string {
  return `${folio}::${schemeName}`;
}

function formatAlpha(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  const sign = value >= 0 ? "+" : "";
  return `${sign}${value.toFixed(2)}%`;
}

function formatXirr(value: number | null | undefined, reason: string | null | undefined): string {
  if (value === null || value === undefined) return reason ? `n/a (${reason})` : "n/a";
  return `${(value * 100).toFixed(2)}%`;
}

function formatPaise(paise: number): string {
  return `Rs ${(paise / 100).toFixed(2)}`;
}

export default function Funds() {
  const [rows, setRows] = useState<FundRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());

  useEffect(() => {
    Promise.all([getVerdicts(), getBenchmarkComparisons(), getDirectVsRegularAudit()])
      .then(([verdicts, comparisons, flags]) => {
        const byKey = new Map<string, FundRow>();
        for (const v of verdicts) {
          byKey.set(key(v.folio, v.scheme_name), {
            folio: v.folio,
            schemeName: v.scheme_name,
            verdict: v,
            comparison: undefined,
            planType: undefined,
          });
        }
        for (const c of comparisons) {
          const k = key(c.folio, c.scheme_name);
          const existing = byKey.get(k);
          if (existing) existing.comparison = c;
          else
            byKey.set(k, {
              folio: c.folio,
              schemeName: c.scheme_name,
              verdict: undefined,
              comparison: c,
              planType: undefined,
            });
        }
        for (const f of flags) {
          const k = key(f.folio, f.scheme_name);
          const existing = byKey.get(k);
          if (existing) existing.planType = f.plan_type;
          else
            byKey.set(k, {
              folio: f.folio,
              schemeName: f.scheme_name,
              verdict: undefined,
              comparison: undefined,
              planType: f.plan_type,
            });
        }
        setRows(Array.from(byKey.values()));
      })
      .catch((err: unknown) => setError(err instanceof Error ? err.message : "Unknown error"));
  }, []);

  function toggle(k: string) {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(k)) next.delete(k);
      else next.add(k);
      return next;
    });
  }

  if (error) {
    return (
      <div>
        <h1>Funds</h1>
        <div className="card">
          <p>Failed to load: {error}</p>
        </div>
      </div>
    );
  }

  if (rows === null) {
    return (
      <div>
        <h1>Funds</h1>
        <p className="muted">Loading…</p>
      </div>
    );
  }

  return (
    <div>
      <h1>Funds</h1>
      {rows.length === 0 && (
        <div className="card">
          <p className="muted">No holdings found.</p>
        </div>
      )}
      {rows.map((row) => {
        const k = key(row.folio, row.schemeName);
        const isOpen = expanded.has(k);
        return (
          <div key={k} className="card">
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: 8 }}>
              <div>
                <strong>{row.schemeName}</strong>
                <span className="muted"> ({row.folio})</span>
                {row.planType && (
                  <span
                    className={`badge ${row.planType === "regular" ? "badge-regular" : row.planType === "direct" ? "badge-direct" : "badge-neutral"}`}
                    style={{ marginLeft: 8 }}
                  >
                    {row.planType}
                  </span>
                )}
              </div>
              {row.verdict && (
                <div>
                  <span className={`badge ${row.verdict.verdict_type === "sell" ? "badge-sell" : "badge-hold"}`}>
                    {row.verdict.verdict_type}
                  </span>
                  {row.verdict.overall_score !== null && (
                    <span className="muted" style={{ marginLeft: 8 }}>
                      {row.verdict.overall_score.toFixed(0)}/100 · confidence{" "}
                      {row.verdict.confidence_pct.toFixed(0)}%
                    </span>
                  )}
                </div>
              )}
            </div>

            {row.comparison && (
              <p className="muted" style={{ margin: "10px 0" }}>
                Fund XIRR: {formatXirr(row.comparison.fund_xirr, row.comparison.fund_xirr_reason)}
                {" — "}
                Benchmark ({row.comparison.benchmark_id ?? "n/a"}):{" "}
                {formatXirr(row.comparison.benchmark_xirr, row.comparison.benchmark_xirr_reason)}
                {" — "}
                Alpha: {formatAlpha(row.comparison.alpha_pct)}
              </p>
            )}

            {row.verdict && (
              <>
                <button onClick={() => toggle(k)}>
                  {isOpen ? "Hide" : "Show"} rubric details
                </button>
                {isOpen && (
                  <div style={{ marginTop: 12 }}>
                    <p>{row.verdict.reasoning}</p>
                    {row.verdict.counterfactual && <p className="muted">{row.verdict.counterfactual}</p>}
                    {row.verdict.blocked_reason && (
                      <p className="muted">Blocked: {row.verdict.blocked_reason}</p>
                    )}
                    <table>
                      <thead>
                        <tr>
                          <th>Component</th>
                          <th>Score</th>
                          <th>Weight</th>
                          <th>Reason</th>
                        </tr>
                      </thead>
                      <tbody>
                        {row.verdict.components.map((c) => (
                          <tr key={c.name}>
                            <td>{c.name}</td>
                            <td>{c.score !== null ? c.score.toFixed(0) : "n/a"}</td>
                            <td>{c.weight}</td>
                            <td>{c.reason ?? "—"}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                    <p className="muted" style={{ marginTop: 8 }}>
                      Not scored (no data source): {row.verdict.omitted_components.join(", ")}
                    </p>
                    {row.verdict.tax_impact && (
                      <div style={{ marginTop: 12 }}>
                        <h3>Tax impact of selling today</h3>
                        <p>Tax owed: {formatPaise(row.verdict.tax_impact.tax_owed_paise)}</p>
                        <p className="muted">{row.verdict.tax_impact.breakeven_note}</p>
                      </div>
                    )}
                  </div>
                )}
              </>
            )}
          </div>
        );
      })}
    </div>
  );
}
