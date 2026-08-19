/** Funds page, "By broker" tab: active holdings grouped by the platform
 * their transactions were imported from, with per-source subtotals.
 */
import { Link } from "react-router-dom";
import {
  formatPaise,
  formatPct,
  formatXirrFraction,
  key,
  sourceCostBasis,
  sourceValue,
  type FundRow,
} from "./fundsShared";

export function BrokerBreakdown({ rows }: { rows: FundRow[] }) {
  const bySource = new Map<string, FundRow[]>();
  for (const row of rows) {
    const source = row.metrics?.source ?? "Unknown";
    bySource.set(source, [...(bySource.get(source) ?? []), row]);
  }
  const groups = [...bySource.entries()].sort(
    (a, b) => sourceValue(b[1]) - sourceValue(a[1]),
  );
  const total = rows.reduce((sum, r) => sum + (r.metrics?.current_value_paise ?? 0), 0);

  if (groups.length === 0) {
    return (
      <div className="card">
        <p className="muted">No active holdings.</p>
      </div>
    );
  }

  return (
    <>
      {groups.map(([source, groupRows]) => {
        const value = sourceValue(groupRows);
        const invested = sourceCostBasis(groupRows);
        const returns = value - invested;
        return (
          <div className="card" key={source}>
            <div
              style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}
            >
              <h3>{source}</h3>
              <div>
                {formatPaise(value)}
                <span className="muted"> current</span>
                <span className="muted">
                  {" "}
                  · {groupRows.length} fund{groupRows.length === 1 ? "" : "s"} ·{" "}
                  {total > 0 ? ((value / total) * 100).toFixed(1) : "0.0"}% of portfolio
                </span>
              </div>
            </div>
            <div style={{ display: "flex", gap: 20, flexWrap: "wrap", marginBottom: 12 }}>
              <span className="muted">
                Invested <strong>{formatPaise(invested)}</strong>
              </span>
              <span className="muted">
                Returns{" "}
                <strong className={returns >= 0 ? "positive" : "negative"}>
                  {returns >= 0 ? "+" : "−"}
                  {formatPaise(Math.abs(returns))}
                </strong>
                {invested > 0 && (
                  <>
                    {" "}
                    ({returns >= 0 ? "+" : "−"}
                    {Math.abs((returns / invested) * 100).toFixed(1)}%)
                  </>
                )}
              </span>
            </div>
            <table>
              <thead>
                <tr>
                  <th>Fund name</th>
                  <th>XIRR</th>
                  <th>Returns</th>
                  <th>Current (Invested)</th>
                </tr>
              </thead>
              <tbody>
                {[...groupRows]
                  .sort(
                    (a, b) =>
                      (b.metrics?.current_value_paise ?? 0) -
                      (a.metrics?.current_value_paise ?? 0),
                  )
                  .map((row) => (
                    <tr key={key(row.folio, row.schemeName)}>
                      <td>
                        {row.amfiCode ? (
                          <Link to={`/funds/${encodeURIComponent(row.amfiCode)}`}>
                            {row.schemeName}
                          </Link>
                        ) : (
                          row.schemeName
                        )}
                        <span className="muted"> ({row.folio})</span>
                      </td>
                      <td>{formatXirrFraction(row.metrics?.xirr, row.metrics?.xirr_reason)}</td>
                      <td>{formatPct(row.metrics?.absolute_return_pct)}</td>
                      <td>
                        {formatPaise(row.metrics?.current_value_paise)}
                        <span className="muted">
                          {" "}
                          ({formatPaise(row.metrics?.cost_basis_paise)})
                        </span>
                      </td>
                    </tr>
                  ))}
              </tbody>
            </table>
          </div>
        );
      })}
    </>
  );
}


