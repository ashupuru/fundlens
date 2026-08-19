/** Shared types and formatters for the Funds views. Kept separate so the
 * page and the broker breakdown can both use them without either importing
 * the other (and so each file exports only components).
 */
import type { components } from "../api/schema";

export type Verdict = components["schemas"]["VerdictResponse"];
export type HoldingMetrics = components["schemas"]["HoldingMetricsResponse"];
export type PortfolioMetrics = components["schemas"]["PortfolioMetricsResponse"];

export type FundRow = {
  folio: string;
  schemeName: string;
  schemeKey: string | undefined;
  amfiCode: string | null;
  planType: string | undefined;
  verdict: Verdict | undefined;
  metrics: HoldingMetrics | undefined;
};

export type SortKey = "schemeName" | "xirr" | "absoluteReturn" | "currentValue";
export type SortDirection = "asc" | "desc";

// Mirrors compute/metrics.py's source labels. "Direct / AMC folio" means the
// position was imported from your CAS under an AMC folio -- NOT that it was
// never bought through a broker. A CAS comes from the RTAs and lists
// platform-bought holdings too.
export const BROKER_SOURCES = new Set(["Dezerv", "Zerodha"]);

export function key(folio: string, schemeName: string): string {
  return `${folio}::${schemeName}`;
}

export function isActive(row: FundRow): boolean {
  return row.metrics ? !row.metrics.is_fully_exited : false;
}

export function formatPct(value: number | null | undefined, reason?: string | null): string {
  if (value === null || value === undefined) return reason ? `n/a (${reason})` : "n/a";
  return `${value >= 0 ? "+" : ""}${value.toFixed(2)}%`;
}

export function formatXirrFraction(
  value: number | null | undefined,
  reason?: string | null,
): string {
  if (value === null || value === undefined) return reason ? `n/a (${reason})` : "n/a";
  return formatPct(value * 100);
}

export function formatPaise(paise: number | null | undefined): string {
  if (paise === null || paise === undefined) return "—";
  return `₹${(paise / 100).toLocaleString("en-IN", { maximumFractionDigits: 0 })}`;
}

export function sortValue(row: FundRow, sortKey: SortKey): number | string {
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

export function sourceValue(rows: FundRow[]): number {
  return rows.reduce((sum, r) => sum + (r.metrics?.current_value_paise ?? 0), 0);
}
