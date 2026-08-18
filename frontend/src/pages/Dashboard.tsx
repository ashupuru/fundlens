import { useEffect, useState } from "react";
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  Cell,
  Legend,
  Line,
  LineChart,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import {
  getAllocationDrift,
  getBenchmarkComparisons,
  getCategoryBreakdown,
  getEquityBlendedBenchmark,
  getHealth,
  getHoldingMetrics,
  getPortfolioBenchmarkComparison,
  getPortfolioMetrics,
  getPortfolioValueHistory,
} from "../api/client";
import type { components } from "../api/schema";

type ApiStatus = "checking" | "connected" | "unreachable";
type PortfolioMetrics = components["schemas"]["PortfolioMetricsResponse"];
type PortfolioBenchmarkComparison = components["schemas"]["PortfolioBenchmarkComparisonResponse"];
type EquityBlendedComparison =
  components["schemas"]["PortfolioEquityBlendedComparisonResponse"];
type CategoryGroup = components["schemas"]["CategoryBreakdownItemResponse"];
type AllocationDrift = components["schemas"]["AllocationDriftResponse"];
type ValuePoint = components["schemas"]["ValuePointResponse"];
type HoldingMetric = components["schemas"]["HoldingMetricsResponse"];
type BenchmarkComparison = components["schemas"]["BenchmarkComparisonResponse"];

const POSITIVE_COLOR = "#2f6b4f";
const NEGATIVE_COLOR = "#b3402c";

// Cycles across chart slices/tiles — chosen to harmonize with the existing
// Monarch-style green/cream theme rather than the raw palette of any
// particular reference design.
const CHART_COLORS = [
  "#1b4332", // brand green
  "#3a6ea5", // blue
  "#c98a2c", // amber
  "#6b4f8a", // purple
  "#2f8a7a", // teal
  "#b3402c", // rust
  "#8a7a2f", // olive
  "#5c6470", // slate
];

function formatPct(value: number | null): string {
  if (value === null) return "—";
  return `${(value * 100).toFixed(2)}%`;
}

function formatGroupPct(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return `${value.toFixed(1)}%`;
}

function formatAlpha(value: number | null): string {
  if (value === null) return "—";
  const sign = value >= 0 ? "+" : "";
  return `${sign}${value.toFixed(2)}%`;
}

function formatPaise(paise: number): string {
  return `₹${(paise / 100).toLocaleString("en-IN", { maximumFractionDigits: 0 })}`;
}

function formatXirr(value: number | null, reason?: string | null): string {
  if (value === null) return reason ? `n/a (${reason})` : "n/a";
  return formatPct(value);
}

function formatDriftPct(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  const sign = value >= 0 ? "+" : "";
  return `${sign}${value.toFixed(1)}%`;
}

function AssetAllocationSection({ groups }: { groups: CategoryGroup[] | null }) {
  if (groups === null) {
    return (
      <div className="card">
        <h3>Asset Allocation</h3>
        <p className="muted">Loading…</p>
      </div>
    );
  }

  if (groups.length === 0) {
    return (
      <div className="card">
        <h3>Asset Allocation</h3>
        <p className="muted">No priced holdings yet.</p>
      </div>
    );
  }

  return (
    <div className="card">
      <h3>Asset Allocation</h3>
      <div style={{ display: "flex", flexWrap: "wrap", gap: 24, alignItems: "center" }}>
        <div style={{ width: 220, height: 220, flexShrink: 0 }}>
          <ResponsiveContainer>
            <PieChart>
              <Pie
                data={groups}
                dataKey="allocation_pct"
                nameKey="label"
                innerRadius={55}
                outerRadius={90}
                paddingAngle={2}
              >
                {groups.map((g, i) => (
                  <Cell key={g.label} fill={CHART_COLORS[i % CHART_COLORS.length]} />
                ))}
              </Pie>
              <Tooltip formatter={(value) => `${Number(value).toFixed(1)}%`} />
            </PieChart>
          </ResponsiveContainer>
        </div>
        <table style={{ flex: 1, minWidth: 240 }}>
          <thead>
            <tr>
              <th>Category</th>
              <th>Allocation</th>
              <th>XIRR</th>
            </tr>
          </thead>
          <tbody>
            {groups.map((g, i) => (
              <tr key={g.label}>
                <td>
                  <span
                    style={{
                      display: "inline-block",
                      width: 10,
                      height: 10,
                      borderRadius: "50%",
                      background: CHART_COLORS[i % CHART_COLORS.length],
                      marginRight: 8,
                    }}
                  />
                  {g.label}
                </td>
                <td>{formatGroupPct(g.allocation_pct)}</td>
                <td className={g.xirr !== null ? (g.xirr >= 0 ? "positive" : "negative") : ""}>
                  {formatXirr(g.xirr, g.xirr_reason)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function CategoryAllocationSection({ groups }: { groups: CategoryGroup[] | null }) {
  if (groups === null) {
    return (
      <div className="card">
        <h3>Category Allocation</h3>
        <p className="muted">Loading…</p>
      </div>
    );
  }

  if (groups.length === 0) {
    return (
      <div className="card">
        <h3>Category Allocation</h3>
        <p className="muted">No priced holdings yet.</p>
      </div>
    );
  }

  return (
    <div className="card">
      <h3>Category Allocation</h3>
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fill, minmax(150px, 1fr))",
          gap: 12,
        }}
      >
        {groups.map((g, i) => (
          <div
            key={g.label}
            style={{
              background: CHART_COLORS[i % CHART_COLORS.length],
              borderRadius: "var(--radius-sm)",
              padding: "12px 14px",
              color: "#fff",
            }}
          >
            <div style={{ fontSize: 13, fontWeight: 600, opacity: 0.9 }}>{g.label}</div>
            <div style={{ fontSize: 22, fontWeight: 700, marginTop: 4 }}>
              {formatGroupPct(g.allocation_pct)}
            </div>
            <div style={{ fontSize: 12, opacity: 0.9, marginTop: 2 }}>
              XIRR {formatXirr(g.xirr, g.xirr_reason)}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

function AllocationDriftSection({
  drift,
  loaded,
}: {
  drift: AllocationDrift | null;
  loaded: boolean;
}) {
  if (!loaded) {
    return (
      <div className="card">
        <h3>Allocation Drift</h3>
        <p className="muted">Loading…</p>
      </div>
    );
  }

  if (drift === null) {
    return (
      <div className="card">
        <h3>Allocation Drift</h3>
        <p className="muted">
          No risk profile set — target allocation isn't configured yet (there's no setup screen
          for this in the UI today; it's set via <code>POST /risk-profile</code>).
        </p>
      </div>
    );
  }

  return (
    <div className="card">
      <h3>Allocation Drift</h3>
      <p className="muted" style={{ marginBottom: 12 }}>
        Persona: {drift.persona}
      </p>
      <table>
        <thead>
          <tr>
            <th>Asset class</th>
            <th>Target</th>
            <th>Actual</th>
            <th>Drift</th>
          </tr>
        </thead>
        <tbody>
          <tr>
            <td>Equity</td>
            <td>{drift.target_equity_pct.toFixed(1)}%</td>
            <td>{formatGroupPct(drift.actual_equity_pct)}</td>
            <td
              className={
                drift.equity_drift_pct !== null
                  ? drift.equity_drift_pct >= 0
                    ? "positive"
                    : "negative"
                  : ""
              }
            >
              {formatDriftPct(drift.equity_drift_pct)}
            </td>
          </tr>
          <tr>
            <td>Debt</td>
            <td>{drift.target_debt_pct.toFixed(1)}%</td>
            <td>{formatGroupPct(drift.actual_debt_pct)}</td>
            <td
              className={
                drift.debt_drift_pct !== null
                  ? drift.debt_drift_pct >= 0
                    ? "positive"
                    : "negative"
                  : ""
              }
            >
              {formatDriftPct(drift.debt_drift_pct)}
            </td>
          </tr>
          <tr>
            <td>Other</td>
            <td>—</td>
            <td>{formatGroupPct(drift.actual_other_pct)}</td>
            <td>—</td>
          </tr>
        </tbody>
      </table>
      {drift.schemes_missing_metadata > 0 && (
        <p className="muted" style={{ marginTop: 12 }}>
          {drift.schemes_missing_metadata} scheme(s) missing category metadata, counted as
          "Other".
        </p>
      )}
      <GlidePathSection glidePath={drift.glide_path} />
    </div>
  );
}

function GlidePathSection({ glidePath }: { glidePath: AllocationDrift["glide_path"] }) {
  if (glidePath.reason === "no_horizon_set") {
    return (
      <p className="muted" style={{ marginTop: 16 }}>
        No horizon set at onboarding — glide-path suggestions need a horizon_years value
        (<code>POST /risk-profile</code>) to estimate years remaining.
      </p>
    );
  }

  const yearsRemaining = glidePath.years_remaining;
  if (yearsRemaining === null || glidePath.suggested_target_equity_pct === null) {
    return null;
  }

  const roundedYears = Math.max(0, Math.round(yearsRemaining));
  const isOnTrack = glidePath.drift_pp === 0;

  return (
    <p className="muted" style={{ marginTop: 16 }}>
      Glide path: ~{roundedYears} year{roundedYears === 1 ? "" : "s"} remaining to your horizon.{" "}
      {isOnTrack ? (
        "Current target allocation is on track — no adjustment suggested."
      ) : (
        <>
          Consider shifting toward{" "}
          <strong>
            {glidePath.suggested_target_equity_pct.toFixed(1)}% equity /{" "}
            {glidePath.suggested_target_debt_pct?.toFixed(1)}% debt
          </strong>{" "}
          (currently {glidePath.current_target_equity_pct.toFixed(1)}% /{" "}
          {glidePath.current_target_debt_pct.toFixed(1)}%).
        </>
      )}
    </p>
  );
}

function computeDrawdownSeries(
  points: ValuePoint[],
): { as_of_date: string; drawdown_pct: number }[] {
  let peak = 0;
  const series: { as_of_date: string; drawdown_pct: number }[] = [];
  for (const p of points) {
    if (p.value_paise <= 0) continue; // no position yet -- nothing to draw down from
    peak = Math.max(peak, p.value_paise);
    series.push({ as_of_date: p.as_of_date, drawdown_pct: ((p.value_paise - peak) / peak) * 100 });
  }
  return series;
}

function PortfolioValueSection({ points, loaded }: { points: ValuePoint[] | null; loaded: boolean }) {
  if (!loaded) {
    return (
      <div className="card">
        <h3>Portfolio Value vs Invested</h3>
        <p className="muted">Loading…</p>
      </div>
    );
  }

  if (points === null || points.length === 0) {
    return (
      <div className="card">
        <h3>Portfolio Value vs Invested</h3>
        <p className="muted">No transaction history yet.</p>
      </div>
    );
  }

  return (
    <div className="card">
      <h3>Portfolio Value vs Invested</h3>
      <div style={{ width: "100%", height: 280 }}>
        <ResponsiveContainer>
          <LineChart data={points}>
            <XAxis dataKey="as_of_date" tick={{ fontSize: 11 }} minTickGap={40} />
            <YAxis tickFormatter={(v: number) => formatPaise(v)} tick={{ fontSize: 11 }} width={90} />
            <Tooltip formatter={(value) => formatPaise(Number(value))} />
            <Legend />
            <Line
              type="monotone"
              dataKey="value_paise"
              name="Value"
              stroke={CHART_COLORS[0]}
              dot={false}
              strokeWidth={2}
            />
            <Line
              type="monotone"
              dataKey="invested_paise"
              name="Invested"
              stroke={CHART_COLORS[1]}
              dot={false}
              strokeWidth={2}
            />
          </LineChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}

function DrawdownSection({ points, loaded }: { points: ValuePoint[] | null; loaded: boolean }) {
  if (!loaded) {
    return (
      <div className="card">
        <h3>Drawdown</h3>
        <p className="muted">Loading…</p>
      </div>
    );
  }

  const series = points ? computeDrawdownSeries(points) : [];
  if (series.length === 0) {
    return (
      <div className="card">
        <h3>Drawdown</h3>
        <p className="muted">No priced value history yet.</p>
      </div>
    );
  }

  return (
    <div className="card">
      <h3>Drawdown</h3>
      <p className="muted" style={{ marginBottom: 12 }}>
        Percentage fall from the portfolio's highest value reached so far, week by week.
      </p>
      <div style={{ width: "100%", height: 220 }}>
        <ResponsiveContainer>
          <AreaChart data={series}>
            <XAxis dataKey="as_of_date" tick={{ fontSize: 11 }} minTickGap={40} />
            <YAxis tickFormatter={(v: number) => `${v.toFixed(0)}%`} tick={{ fontSize: 11 }} width={50} />
            <Tooltip formatter={(value) => `${Number(value).toFixed(2)}%`} />
            <Area
              type="monotone"
              dataKey="drawdown_pct"
              stroke={NEGATIVE_COLOR}
              fill={NEGATIVE_COLOR}
              fillOpacity={0.25}
            />
          </AreaChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}

function FundAllocationSection({
  holdings,
  loaded,
}: {
  holdings: HoldingMetric[] | null;
  loaded: boolean;
}) {
  if (!loaded) {
    return (
      <div className="card">
        <h3>Fund Allocation</h3>
        <p className="muted">Loading…</p>
      </div>
    );
  }

  const priced = (holdings ?? []).filter(
    (h): h is HoldingMetric & { current_value_paise: number } =>
      h.current_value_paise !== null && h.current_value_paise > 0,
  );
  if (priced.length === 0) {
    return (
      <div className="card">
        <h3>Fund Allocation</h3>
        <p className="muted">No priced holdings yet.</p>
      </div>
    );
  }

  const sorted = [...priced].sort((a, b) => b.current_value_paise - a.current_value_paise);
  const top = sorted.slice(0, 10);
  const rest = sorted.slice(10);
  const restValue = rest.reduce((sum, h) => sum + h.current_value_paise, 0);
  const bars = [
    ...top.map((h) => ({ name: h.scheme_name, value: h.current_value_paise })),
    ...(rest.length > 0 ? [{ name: `Others (${rest.length} funds)`, value: restValue }] : []),
  ];

  return (
    <div className="card">
      <h3>Fund Allocation</h3>
      <div style={{ width: "100%", height: Math.max(220, bars.length * 32) }}>
        <ResponsiveContainer>
          <BarChart data={bars} layout="vertical" margin={{ left: 24 }}>
            <XAxis type="number" tickFormatter={(v: number) => formatPaise(v)} tick={{ fontSize: 11 }} />
            <YAxis type="category" dataKey="name" width={200} tick={{ fontSize: 11 }} />
            <Tooltip formatter={(value) => formatPaise(Number(value))} />
            <Bar dataKey="value">
              {bars.map((b, i) => (
                <Cell key={b.name} fill={CHART_COLORS[i % CHART_COLORS.length]} />
              ))}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}

function AmcAllocationSection({ groups, loaded }: { groups: CategoryGroup[] | null; loaded: boolean }) {
  if (!loaded) {
    return (
      <div className="card">
        <h3>AMC Allocation</h3>
        <p className="muted">Loading…</p>
      </div>
    );
  }

  if (groups === null || groups.length === 0) {
    return (
      <div className="card">
        <h3>AMC Allocation</h3>
        <p className="muted">No priced holdings yet.</p>
      </div>
    );
  }

  const sorted = [...groups].sort((a, b) => b.current_value_paise - a.current_value_paise);

  return (
    <div className="card">
      <h3>AMC Allocation</h3>
      <div style={{ width: "100%", height: Math.max(220, sorted.length * 32) }}>
        <ResponsiveContainer>
          <BarChart data={sorted} layout="vertical" margin={{ left: 24 }}>
            <XAxis type="number" tickFormatter={(v: number) => formatPaise(v)} tick={{ fontSize: 11 }} />
            <YAxis type="category" dataKey="label" width={160} tick={{ fontSize: 11 }} />
            <Tooltip formatter={(value) => formatPaise(Number(value))} />
            <Bar dataKey="current_value_paise">
              {sorted.map((g, i) => (
                <Cell key={g.label} fill={CHART_COLORS[i % CHART_COLORS.length]} />
              ))}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}

function AlphaVsBenchmarkSection({
  comparisons,
  loaded,
}: {
  comparisons: BenchmarkComparison[] | null;
  loaded: boolean;
}) {
  if (!loaded) {
    return (
      <div className="card">
        <h3>Alpha vs Benchmark</h3>
        <p className="muted">Loading…</p>
      </div>
    );
  }

  const resolved = (comparisons ?? []).filter(
    (c): c is BenchmarkComparison & { alpha_pct: number } => c.alpha_pct !== null,
  );
  if (resolved.length === 0) {
    return (
      <div className="card">
        <h3>Alpha vs Benchmark</h3>
        <p className="muted">No positions with a resolved benchmark yet.</p>
      </div>
    );
  }

  const sorted = [...resolved].sort((a, b) => a.alpha_pct - b.alpha_pct);
  const key = (c: BenchmarkComparison) => `${c.folio}-${c.scheme_name}`;
  const picked = new Map<string, (typeof sorted)[number]>();
  for (const c of [...sorted.slice(0, 5), ...sorted.slice(-5)]) {
    picked.set(key(c), c);
  }
  const bars = [...picked.values()].sort((a, b) => a.alpha_pct - b.alpha_pct);

  return (
    <div className="card">
      <h3>Alpha vs Benchmark</h3>
      <p className="muted" style={{ marginBottom: 12 }}>
        Biggest outperformers and underperformers vs each fund's category-appropriate benchmark.
      </p>
      <div style={{ width: "100%", height: Math.max(220, bars.length * 32) }}>
        <ResponsiveContainer>
          <BarChart data={bars} layout="vertical" margin={{ left: 24 }}>
            <XAxis type="number" tickFormatter={(v: number) => `${v.toFixed(0)}%`} tick={{ fontSize: 11 }} />
            <YAxis type="category" dataKey="scheme_name" width={200} tick={{ fontSize: 11 }} />
            <Tooltip formatter={(value) => formatAlpha(Number(value))} />
            <Bar dataKey="alpha_pct">
              {bars.map((c) => (
                <Cell key={key(c)} fill={c.alpha_pct >= 0 ? POSITIVE_COLOR : NEGATIVE_COLOR} />
              ))}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}

function EquityBlendedBenchmarkSection({
  comparison,
  loaded,
}: {
  comparison: EquityBlendedComparison | null;
  loaded: boolean;
}) {
  if (!loaded) {
    return (
      <div className="card">
        <h3>Equity Blended Benchmark</h3>
        <p className="muted">Loading…</p>
      </div>
    );
  }

  if (comparison === null || comparison.xirr_reason === "no_equity_holdings") {
    return (
      <div className="card">
        <h3>Equity Blended Benchmark</h3>
        <p className="muted">No open equity holdings to compare.</p>
      </div>
    );
  }

  const alphaClass =
    comparison.alpha_pct === null ? "" : comparison.alpha_pct >= 0 ? "positive" : "negative";

  return (
    <div className="card">
      <h3>Equity Blended Benchmark</h3>
      <div className="card-grid">
        <div className="stat">
          <div className="stat-label">Equity XIRR</div>
          <div className="stat-value">
            {comparison.xirr !== null ? formatPct(comparison.xirr) : "n/a"}
          </div>
        </div>
        <div className="stat">
          <div className="stat-label">Blended benchmark XIRR</div>
          <div className="stat-value">
            {comparison.benchmark_xirr !== null ? formatPct(comparison.benchmark_xirr) : "n/a"}
          </div>
        </div>
        <div className="stat">
          <div className="stat-label">Alpha</div>
          <div className={`stat-value ${alphaClass}`}>{formatAlpha(comparison.alpha_pct)}</div>
        </div>
      </div>
      <p className="muted" style={{ marginTop: 12 }}>
        Blend weights (by current equity value): Large {formatGroupPct(comparison.large_weight_pct)},
        Mid {formatGroupPct(comparison.mid_weight_pct)}, Small{" "}
        {formatGroupPct(comparison.small_weight_pct)}
      </p>
      <p className="muted" style={{ marginTop: 8 }}>
        {comparison.note}
      </p>
    </div>
  );
}

export default function Dashboard() {
  const [apiStatus, setApiStatus] = useState<ApiStatus>("checking");
  const [metrics, setMetrics] = useState<PortfolioMetrics | null>(null);
  const [comparison, setComparison] = useState<PortfolioBenchmarkComparison | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [categoryGroups, setCategoryGroups] = useState<CategoryGroup[] | null>(null);
  const [subCategoryGroups, setSubCategoryGroups] = useState<CategoryGroup[] | null>(null);
  const [drift, setDrift] = useState<AllocationDrift | null>(null);
  const [driftLoaded, setDriftLoaded] = useState(false);
  const [blendedComparison, setBlendedComparison] = useState<EquityBlendedComparison | null>(
    null,
  );
  const [blendedComparisonLoaded, setBlendedComparisonLoaded] = useState(false);
  const [valueHistory, setValueHistory] = useState<ValuePoint[] | null>(null);
  const [valueHistoryLoaded, setValueHistoryLoaded] = useState(false);
  const [holdingMetrics, setHoldingMetrics] = useState<HoldingMetric[] | null>(null);
  const [holdingMetricsLoaded, setHoldingMetricsLoaded] = useState(false);
  const [amcGroups, setAmcGroups] = useState<CategoryGroup[] | null>(null);
  const [amcGroupsLoaded, setAmcGroupsLoaded] = useState(false);
  const [benchmarkComparisons, setBenchmarkComparisons] = useState<BenchmarkComparison[] | null>(
    null,
  );
  const [benchmarkComparisonsLoaded, setBenchmarkComparisonsLoaded] = useState(false);

  useEffect(() => {
    getHealth()
      .then(() => setApiStatus("connected"))
      .catch(() => setApiStatus("unreachable"));

    getPortfolioMetrics()
      .then(setMetrics)
      .catch((err: unknown) => setError(err instanceof Error ? err.message : "Unknown error"));

    getPortfolioBenchmarkComparison()
      .then(setComparison)
      .catch((err: unknown) => setError(err instanceof Error ? err.message : "Unknown error"));

    getCategoryBreakdown("category")
      .then(setCategoryGroups)
      .catch(() => setCategoryGroups([]));

    getCategoryBreakdown("sub_category")
      .then(setSubCategoryGroups)
      .catch(() => setSubCategoryGroups([]));

    getAllocationDrift()
      .then((d) => {
        setDrift(d);
        setDriftLoaded(true);
      })
      .catch(() => setDriftLoaded(true));

    getEquityBlendedBenchmark()
      .then((c) => {
        setBlendedComparison(c);
        setBlendedComparisonLoaded(true);
      })
      .catch(() => setBlendedComparisonLoaded(true));

    getPortfolioValueHistory()
      .then((points) => {
        setValueHistory(points);
        setValueHistoryLoaded(true);
      })
      .catch(() => setValueHistoryLoaded(true));

    getHoldingMetrics()
      .then((h) => {
        setHoldingMetrics(h);
        setHoldingMetricsLoaded(true);
      })
      .catch(() => setHoldingMetricsLoaded(true));

    getCategoryBreakdown("amc")
      .then((groups) => {
        setAmcGroups(groups);
        setAmcGroupsLoaded(true);
      })
      .catch(() => setAmcGroupsLoaded(true));

    getBenchmarkComparisons()
      .then((c) => {
        setBenchmarkComparisons(c);
        setBenchmarkComparisonsLoaded(true);
      })
      .catch(() => setBenchmarkComparisonsLoaded(true));
  }, []);

  const alphaClass =
    comparison?.alpha_pct === null || comparison?.alpha_pct === undefined
      ? ""
      : comparison.alpha_pct >= 0
        ? "positive"
        : "negative";

  return (
    <div>
      <h1>Dashboard</h1>
      <p className="muted" style={{ marginBottom: 24 }}>
        API {apiStatus === "connected" ? "connected" : apiStatus}
      </p>

      {error && (
        <div className="card">
          <p>Failed to load portfolio summary: {error}</p>
        </div>
      )}

      {metrics && (
        <>
          <h2>Portfolio</h2>
          <div className="card-grid">
            <div className="stat">
              <div className="stat-label">Current value</div>
              <div className="stat-value">{formatPaise(metrics.current_value_paise)}</div>
            </div>
            <div className="stat">
              <div className="stat-label">Portfolio XIRR</div>
              <div className="stat-value">
                {metrics.xirr !== null ? formatPct(metrics.xirr) : "n/a"}
              </div>
              {metrics.xirr === null && <div className="muted">{metrics.xirr_reason}</div>}
            </div>
            {comparison && (
              <>
                <div className="stat">
                  <div className="stat-label">Benchmark XIRR</div>
                  <div className="stat-value">
                    {comparison.benchmark_xirr !== null
                      ? formatPct(comparison.benchmark_xirr)
                      : "n/a"}
                  </div>
                  <div className="muted">{comparison.benchmark_id}</div>
                </div>
                <div className="stat">
                  <div className="stat-label">Alpha</div>
                  <div className={`stat-value ${alphaClass}`}>
                    {formatAlpha(comparison.alpha_pct)}
                  </div>
                </div>
              </>
            )}
          </div>

          <div className="card">
            <h3>Details</h3>
            <table>
              <tbody>
                <tr>
                  <td>Invested</td>
                  <td>{formatPaise(metrics.invested_paise)}</td>
                </tr>
                <tr>
                  <td>Received (redemptions/dividends)</td>
                  <td>{formatPaise(metrics.received_paise)}</td>
                </tr>
                <tr>
                  <td>Positions missing NAV</td>
                  <td>{metrics.positions_missing_nav}</td>
                </tr>
              </tbody>
            </table>
            {comparison && <p className="muted" style={{ marginTop: 12 }}>{comparison.proxy_note}</p>}
          </div>

          <EquityBlendedBenchmarkSection
            comparison={blendedComparison}
            loaded={blendedComparisonLoaded}
          />

          <AssetAllocationSection groups={categoryGroups} />
          <CategoryAllocationSection groups={subCategoryGroups} />
          <AllocationDriftSection drift={drift} loaded={driftLoaded} />

          <PortfolioValueSection points={valueHistory} loaded={valueHistoryLoaded} />
          <DrawdownSection points={valueHistory} loaded={valueHistoryLoaded} />
          <FundAllocationSection holdings={holdingMetrics} loaded={holdingMetricsLoaded} />
          <AmcAllocationSection groups={amcGroups} loaded={amcGroupsLoaded} />
          <AlphaVsBenchmarkSection
            comparisons={benchmarkComparisons}
            loaded={benchmarkComparisonsLoaded}
          />
        </>
      )}
    </div>
  );
}
