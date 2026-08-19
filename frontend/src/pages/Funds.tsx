import { Fragment, useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { BrokerBreakdown } from "./FundsBroker";
import { SortableHeader } from "./FundsSortableHeader";
import { SourceCell, SourceDatalist } from "./FundsSourceCell";
import {
  BROKER_SOURCES,
  formatPaise,
  formatPct,
  formatXirrFraction,
  isActive,
  key,
  sortValue,
  type FundRow,
  type PortfolioMetrics,
  type SortDirection,
  type SortKey,
} from "./fundsShared";
import {
  getDirectVsRegularAudit,
  getKnownHoldingSources,
  getHoldingMetrics,
  getHoldings,
  getPortfolioMetrics,
  getVerdicts,
} from "../api/client";


const TABS = [
  { key: "active", label: "Active funds" },
  { key: "broker", label: "By broker" },
  { key: "personal", label: "Personal" },
] as const;

type TabKey = (typeof TABS)[number]["key"];



export default function Funds() {
  const [rows, setRows] = useState<FundRow[] | null>(null);
  const [portfolio, setPortfolio] = useState<PortfolioMetrics | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [sort, setSort] = useState<{ key: SortKey; direction: SortDirection }>({
    key: "currentValue",
    direction: "desc",
  });
  const [tab, setTab] = useState<TabKey>("active");
  const [knownSources, setKnownSources] = useState<string[]>([]);
  const [searchParams, setSearchParams] = useSearchParams();
  const amcFilter = searchParams.get("amc");
  const categoryFilter = searchParams.get("category");

  function refresh() {
    getKnownHoldingSources().then(setKnownSources).catch(() => setKnownSources([]));
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
              schemeKey: undefined,
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
        for (const h of holdings) {
          const holdingRow = ensure(h.folio, h.scheme_name);
          holdingRow.amfiCode = h.amfi_code;
          holdingRow.schemeKey = h.scheme_key;
        }

        setRows(Array.from(byKey.values()));
        setPortfolio(portfolioMetrics);
      })
      .catch((err: unknown) => setError(err instanceof Error ? err.message : "Unknown error"));
  }

  useEffect(refresh, []);

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

  // Pre-filtering from a Dashboard chart drill-down (?amc= / ?category=) --
  // category matches either the broad or specific label, since both the
  // Asset Allocation (broad) and Category Allocation (specific) charts link
  // here and the URL alone doesn't say which level a label came from.
  const filteredRows = rows.filter((r) => {
    if (amcFilter && r.metrics?.amc !== amcFilter) return false;
    if (
      categoryFilter &&
      r.metrics?.category_broad !== categoryFilter &&
      r.metrics?.category_specific !== categoryFilter
    ) {
      return false;
    }
    return true;
  });

  // Every tab shows only currently-held positions: a fully-exited fund isn't
  // "active", has no broker allocation worth grouping, and isn't personally
  // held any more. Exited funds keep their realised history on the fund
  // detail page.
  const activeRows = filteredRows.filter(isActive);
  const tabRows =
    tab === "personal"
      ? activeRows.filter((r) => !BROKER_SOURCES.has(r.metrics?.source ?? ""))
      : activeRows;

  const sorted = [...tabRows].sort((a, b) => {
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

      {(amcFilter || categoryFilter) && (
        <p className="muted" style={{ marginBottom: 16 }}>
          Filtered to {amcFilter ? `AMC "${amcFilter}"` : `category "${categoryFilter}"`} —{" "}
          {filteredRows.length} of {rows.length} funds.{" "}
          <a
            href="/funds"
            onClick={(e) => {
              e.preventDefault();
              setSearchParams({});
            }}
          >
            Clear filter
          </a>
        </p>
      )}

      <SourceDatalist sources={knownSources} />

      <div className="dashboard-tabs" role="tablist">
        {TABS.map((t) => (
          <button
            key={t.key}
            type="button"
            role="tab"
            aria-selected={tab === t.key}
            className={`dashboard-tab${tab === t.key ? " active" : ""}`}
            onClick={() => setTab(t.key)}
          >
            {t.label}
          </button>
        ))}
      </div>

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

      {tab === "personal" && (
        <p className="muted" style={{ marginBottom: 16 }}>
          Held under an AMC folio in your own name, rather than through Dezerv or Zerodha. Note a
          CAS comes from the RTAs and lists platform-bought funds too, so this means "held under an
          AMC folio", not "never bought via a broker".
        </p>
      )}

      {tab === "broker" ? (
        <BrokerBreakdown rows={activeRows} />
      ) : sorted.length === 0 ? (
        <div className="card">
          <p className="muted">
            {rows.length === 0 ? "No holdings found." : "No funds match this filter."}
          </p>
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
                <th>Source</th>
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
                        <SourceCell row={row} schemeKey={row.schemeKey} onSaved={refresh} />
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
                        <td colSpan={6}>
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
