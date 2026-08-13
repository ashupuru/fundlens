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
      <main>
        <h1>Funds</h1>
        <p>Failed to load: {error}</p>
      </main>
    );
  }

  if (rows === null) {
    return (
      <main>
        <h1>Funds</h1>
        <p>Loading…</p>
      </main>
    );
  }

  return (
    <main style={{ textAlign: "left", padding: "0 24px" }}>
      <h1>Funds</h1>
      {rows.length === 0 && <p>No holdings found.</p>}
      {rows.map((row) => {
        const k = key(row.folio, row.schemeName);
        const isOpen = expanded.has(k);
        return (
          <div
            key={k}
            style={{ border: "1px solid #ccc", borderRadius: 8, padding: 12, marginBottom: 12 }}
          >
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
              <div>
                <strong>{row.schemeName}</strong> ({row.folio})
                {row.planType && (
                  <span style={{ marginLeft: 8, fontSize: 12 }}>
                    [{row.planType === "regular" ? "REGULAR PLAN" : row.planType.toUpperCase()}]
                  </span>
                )}
              </div>
              {row.verdict && (
                <span style={{ fontWeight: "bold" }}>
                  {row.verdict.verdict_type.toUpperCase()}
                  {row.verdict.overall_score !== null &&
                    ` (${row.verdict.overall_score.toFixed(0)}/100, confidence ${row.verdict.confidence_pct.toFixed(0)}%)`}
                </span>
              )}
            </div>

            {row.comparison && (
              <p style={{ margin: "8px 0" }}>
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
                  <div style={{ marginTop: 8 }}>
                    <p>{row.verdict.reasoning}</p>
                    {row.verdict.counterfactual && <p>{row.verdict.counterfactual}</p>}
                    {row.verdict.blocked_reason && (
                      <p>Blocked: {row.verdict.blocked_reason}</p>
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
                    <p style={{ fontSize: 12 }}>
                      Not scored (no data source): {row.verdict.omitted_components.join(", ")}
                    </p>
                    {row.verdict.tax_impact && (
                      <div>
                        <h3>Tax impact of selling today</h3>
                        <p>Tax owed: {formatPaise(row.verdict.tax_impact.tax_owed_paise)}</p>
                        <p>{row.verdict.tax_impact.breakeven_note}</p>
                      </div>
                    )}
                  </div>
                )}
              </>
            )}
          </div>
        );
      })}
    </main>
  );
}
