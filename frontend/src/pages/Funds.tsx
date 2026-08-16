import { Fragment, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import {
  getDirectVsRegularAudit,
  getHoldingMetrics,
  getHoldings,
  getPortfolioMetrics,
  getVerdicts,
} from "../api/client";
import type { components } from "../api/schema";

type Verdict = components["schemas"]["VerdictResponse"];
type HoldingMetrics = components["schemas"]["HoldingMetricsResponse"];
type PortfolioMetrics = components["schemas"]["PortfolioMetricsResponse"];

type FundRow = {
  folio: string;
  schemeName: string;
  amfiCode: string | null;
  planType: string | undefined;
  verdict: Verdict | undefined;
  metrics: HoldingMetrics | undefined;
};

type SortKey = "schemeName" | "xirr" | "absoluteReturn" | "currentValue";
type SortDirection = "asc" | "desc";

function key(folio: string, schemeName: string): string {
  return `${folio}::${schemeName}`;
}

function formatPct(value: number | null | undefined, reason?: string | null): string {
  if (value === null || value === undefined) return reason ? `n/a (${reason})` : "n/a";
  return `${value >= 0 ? "+" : ""}${value.toFixed(2)}%`;
}

function formatXirrFraction(value: number | null | undefined, reason?: string | null): string {
  if (value === null || value === undefined) return reason ? `n/a (${reason})` : "n/a";
  return formatPct(value * 100);
}

function formatPaise(paise: number | null | undefined): string {
  if (paise === null || paise === undefined) return "—";
  return `₹${(paise / 100).toLocaleString("en-IN", { maximumFractionDigits: 0 })}`;
}

function sortValue(row: FundRow, sortKey: SortKey): number | string {
  switch (sortKey) {
    case "schemeName":
      return row.schemeName.toLowerCase();
    case "xirr":
      return row.metrics?.xirr ?? Number.NEGATIVE_INFINITY;
    case "absoluteReturn":
      return row.metrics?.absolute_return_pct ?? Number.NEGATIVE_INFINITY;
    case "currentValue":
      return row.metrics?.current_value_paise ?? Number.NEGATIVE_INFINITY;
  }
}

function SortableHeader({
  label,
  sortKey,
  activeSort,
  onSort,
}: {
  label: string;
  sortKey: SortKey;
  activeSort: { key: SortKey; direction: SortDirection };
  onSort: (key: SortKey) => void;
}) {
  const isActive = activeSort.key === sortKey;
  return (
    <th
      onClick={() => onSort(sortKey)}
      style={{ cursor: "pointer", userSelect: "none" }}
      title="Click to sort"
    >
      {label} {isActive ? (activeSort.direction === "asc" ? "▲" : "▼") : ""}
    </th>
  );
}

export default function Funds() {
  const [rows, setRows] = useState<FundRow[] | null>(null);
  const [portfolio, setPortfolio] = useState<PortfolioMetrics | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [sort, setSort] = useState<{ key: SortKey; direction: SortDirection }>({
    key: "currentValue",
    direction: "desc",
  });

  useEffect(() => {
    Promise.all([
      getVerdicts(),
      getDirectVsRegularAudit(),
      getHoldings(),
      getHoldingMetrics(),
      getPortfolioMetrics(),
    ])
      .then(([verdicts, flags, holdings, metricsRows, portfolioMetrics]) => {
        const byKey = new Map<string, FundRow>();
        const ensure = (folio: string, schemeName: string): FundRow => {
          const k = key(folio, schemeName);
          let row = byKey.get(k);
          if (!row) {
            row = {
              folio,
              schemeName,
              amfiCode: null,
              planType: undefined,
              verdict: undefined,
              metrics: undefined,
            };
            byKey.set(k, row);
          }
          return row;
        };

        for (const m of metricsRows) ensure(m.folio, m.scheme_name).metrics = m;
        for (const v of verdicts) ensure(v.folio, v.scheme_name).verdict = v;
        for (const f of flags) ensure(f.folio, f.scheme_name).planType = f.plan_type;
        for (const h of holdings) ensure(h.folio, h.scheme_name).amfiCode = h.amfi_code;

        setRows(Array.from(byKey.values()));
        setPortfolio(portfolioMetrics);
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

  function onSort(sortKey: SortKey) {
    setSort((prev) =>
      prev.key === sortKey
        ? { key: sortKey, direction: prev.direction === "asc" ? "desc" : "asc" }
        : { key: sortKey, direction: "desc" },
    );
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

  if (rows === null || portfolio === null) {
    return (
      <div>
        <h1>Funds</h1>
        <p className="muted">Loading…</p>
      </div>
    );
  }

  const sorted = [...rows].sort((a, b) => {
    const av = sortValue(a, sort.key);
    const bv = sortValue(b, sort.key);
    const cmp = av < bv ? -1 : av > bv ? 1 : 0;
    return sort.direction === "asc" ? cmp : -cmp;
  });

  // "Invested value" and "Total returns" must share the same basis so the
  // three headline stats reconcile on screen the way Groww's own dashboard
  // does (verified against the reference screenshot: Total returns there is
  // exactly Current value − Invested value). Both are scoped to currently
  // active funds only -- cost_basis_paise is already 0 for a fully-exited
  // position, so this naturally excludes them, same as "Invested value".
  // Realized gains from exited funds still show up per-fund in the table
  // below (each row's own return/XIRR reflects its full history); they're
  // deliberately not blended into this portfolio-wide headline number.
  const activeCostBasis = rows.reduce((sum, r) => sum + (r.metrics?.cost_basis_paise ?? 0), 0);
  const totalReturns = portfolio.current_value_paise - activeCostBasis;

  return (
    <div>
      <h1>Funds</h1>

      <div className="card-grid">
        <div className="stat">
          <div className="stat-label">Current value</div>
          <div className="stat-value">{formatPaise(portfolio.current_value_paise)}</div>
        </div>
        <div className="stat">
          <div className="stat-label">Invested value</div>
          <div className="stat-value">{formatPaise(activeCostBasis)}</div>
        </div>
        <div className="stat">
          <div className="stat-label">Total returns</div>
          <div className={`stat-value ${totalReturns >= 0 ? "positive" : "negative"}`}>
            {formatPaise(totalReturns)}
          </div>
        </div>
        <div className="stat">
          <div className="stat-label">XIRR</div>
          <div className="stat-value">
            {formatXirrFraction(portfolio.xirr, portfolio.xirr_reason)}
          </div>
        </div>
      </div>

      {rows.length === 0 ? (
        <div className="card">
          <p className="muted">No holdings found.</p>
        </div>
      ) : (
        <div className="card">
          <table>
            <thead>
              <tr>
                <SortableHeader label="Fund name" sortKey="schemeName" activeSort={sort} onSort={onSort} />
                <SortableHeader label="XIRR" sortKey="xirr" activeSort={sort} onSort={onSort} />
                <SortableHeader
                  label="Returns"
                  sortKey="absoluteReturn"
                  activeSort={sort}
                  onSort={onSort}
                />
                <SortableHeader
                  label="Current (Invested)"
                  sortKey="currentValue"
                  activeSort={sort}
                  onSort={onSort}
                />
                <th>Verdict</th>
              </tr>
            </thead>
            <tbody>
              {sorted.map((row) => {
                const k = key(row.folio, row.schemeName);
                const isOpen = expanded.has(k);
                return (
                  <Fragment key={k}>
                    <tr>
                      <td>
                        {row.amfiCode ? (
                          <Link to={`/funds/${encodeURIComponent(row.amfiCode)}`}>
                            {row.schemeName}
                          </Link>
                        ) : (
                          row.schemeName
                        )}
                        <span className="muted"> ({row.folio})</span>
                        {row.planType && (
                          <span
                            className={`badge ${row.planType === "regular" ? "badge-regular" : row.planType === "direct" ? "badge-direct" : "badge-neutral"}`}
                            style={{ marginLeft: 8 }}
                          >
                            {row.planType}
                          </span>
                        )}
                      </td>
                      <td>{formatXirrFraction(row.metrics?.xirr, row.metrics?.xirr_reason)}</td>
                      <td>{formatPct(row.metrics?.absolute_return_pct)}</td>
                      <td>
                        {formatPaise(row.metrics?.current_value_paise)}
                        <span className="muted"> ({formatPaise(row.metrics?.cost_basis_paise)})</span>
                      </td>
                      <td>
                        {row.verdict ? (
                          <button onClick={() => toggle(k)}>
                            <span
                              className={`badge ${row.verdict.verdict_type === "sell" ? "badge-sell" : "badge-hold"}`}
                            >
                              {row.verdict.verdict_type}
                            </span>
                            {isOpen ? " Hide" : " Details"}
                          </button>
                        ) : (
                          "—"
                        )}
                      </td>
                    </tr>
                    {isOpen && row.verdict && (
                      <tr>
                        <td colSpan={5}>
                          <div style={{ padding: "8px 0" }}>
                            {row.verdict.overall_score !== null && (
                              <p className="muted">
                                {row.verdict.overall_score.toFixed(0)}/100 · confidence{" "}
                                {row.verdict.confidence_pct.toFixed(0)}%
                              </p>
                            )}
                            <p>{row.verdict.reasoning}</p>
                            {row.verdict.counterfactual && (
                              <p className="muted">{row.verdict.counterfactual}</p>
                            )}
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
                        </td>
                      </tr>
                    )}
                  </Fragment>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
