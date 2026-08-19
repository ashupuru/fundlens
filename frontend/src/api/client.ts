import type { components, operations } from "./schema";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";

type HealthResponse =
  operations["health_health_get"]["responses"][200]["content"]["application/json"];

export async function getHealth(): Promise<HealthResponse> {
  const response = await fetch(`${API_BASE_URL}/health`);
  if (!response.ok) {
    throw new Error(`Health check failed: ${response.status}`);
  }
  return response.json() as Promise<HealthResponse>;
}

type DataQualitySummaryResponse =
  operations["summary_data_quality_summary_get"]["responses"][200]["content"]["application/json"];

export async function getDataQualitySummary(): Promise<DataQualitySummaryResponse> {
  const response = await fetch(`${API_BASE_URL}/data-quality/summary`);
  if (!response.ok) {
    throw new Error(`Data quality summary failed: ${response.status}`);
  }
  return response.json() as Promise<DataQualitySummaryResponse>;
}

type PortfolioMetricsResponse =
  operations["portfolio_metrics_portfolio_metrics_portfolio_get"]["responses"][200]["content"]["application/json"];

export async function getPortfolioMetrics(): Promise<PortfolioMetricsResponse> {
  const response = await fetch(`${API_BASE_URL}/portfolio/metrics/portfolio`);
  if (!response.ok) {
    throw new Error(`Portfolio metrics failed: ${response.status}`);
  }
  return response.json() as Promise<PortfolioMetricsResponse>;
}

type CategoryBreakdownResponse =
  operations["breakdown_portfolio_breakdown_get"]["responses"][200]["content"]["application/json"];

export async function getCategoryBreakdown(
  level: "category" | "sub_category" | "amc",
): Promise<CategoryBreakdownResponse> {
  const response = await fetch(`${API_BASE_URL}/portfolio/breakdown?level=${level}`);
  if (!response.ok) {
    throw new Error(`Category breakdown failed: ${response.status}`);
  }
  return response.json() as Promise<CategoryBreakdownResponse>;
}

type PortfolioValueHistoryResponse =
  operations["value_history_portfolio_value_history_get"]["responses"][200]["content"]["application/json"];

export async function getPortfolioValueHistory(): Promise<PortfolioValueHistoryResponse> {
  const response = await fetch(`${API_BASE_URL}/portfolio/value-history`);
  if (!response.ok) {
    throw new Error(`Portfolio value history failed: ${response.status}`);
  }
  return response.json() as Promise<PortfolioValueHistoryResponse>;
}

type AllocationDriftResponse =
  operations["drift_allocation_drift_get"]["responses"][200]["content"]["application/json"];

export async function getAllocationDrift(): Promise<AllocationDriftResponse | null> {
  const response = await fetch(`${API_BASE_URL}/allocation/drift`);
  if (response.status === 404) {
    return null; // no risk profile set yet -- an expected state, not an error
  }
  if (!response.ok) {
    throw new Error(`Allocation drift failed: ${response.status}`);
  }
  return response.json() as Promise<AllocationDriftResponse>;
}

export type RiskProfile = components["schemas"]["RiskProfileResponse"];

export async function getRiskProfile(): Promise<RiskProfile | null> {
  const response = await fetch(`${API_BASE_URL}/risk-profile`);
  if (!response.ok) {
    throw new Error(`Failed to load risk profile: ${await detailOrStatus(response)}`);
  }
  return response.json() as Promise<RiskProfile | null>;
}

export async function setRiskProfile(request: {
  persona: string;
  target_equity_pct?: string | null;
  target_debt_pct?: string | null;
  horizon_years?: number | null;
  drawdown_tolerance?: string | null;
  income_stability?: string | null;
  has_dependents?: boolean | null;
}): Promise<RiskProfile> {
  const response = await fetch(`${API_BASE_URL}/risk-profile`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request),
  });
  if (!response.ok) {
    throw new Error(`Failed to save risk profile: ${await detailOrStatus(response)}`);
  }
  return response.json() as Promise<RiskProfile>;
}

export type RetirementProfile = components["schemas"]["RetirementProfileResponse"];
export type RetirementProjection = components["schemas"]["RetirementProjectionResponse"];
export type RetirementYear = components["schemas"]["RetirementYearResponse"];

export async function getRetirementProfile(): Promise<RetirementProfile> {
  const response = await fetch(`${API_BASE_URL}/retirement/profile`);
  if (!response.ok) {
    throw new Error(`Failed to load retirement profile: ${await detailOrStatus(response)}`);
  }
  return response.json() as Promise<RetirementProfile>;
}

export async function setRetirementProfile(request: {
  current_age?: number | null;
  retirement_age?: number | null;
  life_expectancy?: number | null;
  annual_investment_rupees?: string | null;
  annual_investment_step_up_pct?: string | null;
  monthly_withdrawal_today_rupees?: string | null;
  post_retirement_return_pct?: string | null;
}): Promise<RetirementProfile> {
  const response = await fetch(`${API_BASE_URL}/retirement/profile`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request),
  });
  if (!response.ok) {
    throw new Error(`Failed to save retirement profile: ${await detailOrStatus(response)}`);
  }
  return response.json() as Promise<RetirementProfile>;
}

export type RetirementScenario = components["schemas"]["RetirementScenarioResponse"];

export async function getRetirementScenarios(): Promise<RetirementScenario[]> {
  const response = await fetch(`${API_BASE_URL}/retirement/scenarios`);
  if (!response.ok) {
    throw new Error(`Failed to load retirement scenarios: ${await detailOrStatus(response)}`);
  }
  return response.json() as Promise<RetirementScenario[]>;
}

export type RetirementMonteCarlo = components["schemas"]["RetirementMonteCarloResponse"];

export async function getRetirementMonteCarlo(): Promise<RetirementMonteCarlo> {
  const response = await fetch(`${API_BASE_URL}/retirement/monte-carlo`);
  if (!response.ok) {
    throw new Error(`Retirement simulation failed: ${await detailOrStatus(response)}`);
  }
  return response.json() as Promise<RetirementMonteCarlo>;
}

export type RetirementWhatIf = {
  retirement_age?: number | null;
  annual_investment_rupees?: string | null;
  monthly_withdrawal_today_rupees?: string | null;
  return_delta_pct?: string | null;
};

export async function getRetirementProjection(
  whatIf?: RetirementWhatIf,
): Promise<RetirementProjection> {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(whatIf ?? {})) {
    if (value !== null && value !== undefined && value !== "") {
      params.append(key, String(value));
    }
  }
  const query = params.toString();
  const response = await fetch(
    `${API_BASE_URL}/retirement/projection${query ? `?${query}` : ""}`,
  );
  if (!response.ok) {
    throw new Error(`Failed to load retirement projection: ${await detailOrStatus(response)}`);
  }
  return response.json() as Promise<RetirementProjection>;
}

type PortfolioBenchmarkComparisonResponse =
  operations["portfolio_comparison_benchmarks_comparison_portfolio_get"]["responses"][200]["content"]["application/json"];

export async function getPortfolioBenchmarkComparison(): Promise<PortfolioBenchmarkComparisonResponse> {
  const response = await fetch(`${API_BASE_URL}/benchmarks/comparison/portfolio`);
  if (!response.ok) {
    throw new Error(`Portfolio benchmark comparison failed: ${response.status}`);
  }
  return response.json() as Promise<PortfolioBenchmarkComparisonResponse>;
}

type BenchmarkComparisonResponse =
  operations["comparison_benchmarks_comparison_get"]["responses"][200]["content"]["application/json"];

export async function getBenchmarkComparisons(): Promise<BenchmarkComparisonResponse> {
  const response = await fetch(`${API_BASE_URL}/benchmarks/comparison`);
  if (!response.ok) {
    throw new Error(`Benchmark comparison failed: ${response.status}`);
  }
  return response.json() as Promise<BenchmarkComparisonResponse>;
}

type EquityBlendedBenchmarkComparisonResponse =
  operations["equity_blended_comparison_benchmarks_comparison_equity_blended_get"]["responses"][200]["content"]["application/json"];

export async function getEquityBlendedBenchmark(): Promise<EquityBlendedBenchmarkComparisonResponse> {
  const response = await fetch(`${API_BASE_URL}/benchmarks/comparison/equity-blended`);
  if (!response.ok) {
    throw new Error(`Equity blended benchmark comparison failed: ${response.status}`);
  }
  return response.json() as Promise<EquityBlendedBenchmarkComparisonResponse>;
}

type VerdictsResponse =
  operations["verdicts_verdicts_get"]["responses"][200]["content"]["application/json"];

export async function getVerdicts(): Promise<VerdictsResponse> {
  const response = await fetch(`${API_BASE_URL}/verdicts`);
  if (!response.ok) {
    throw new Error(`Verdicts failed: ${response.status}`);
  }
  return response.json() as Promise<VerdictsResponse>;
}

type DirectVsRegularAuditResponse =
  operations["direct_vs_regular_audit_audit_direct_vs_regular_get"]["responses"][200]["content"]["application/json"];

export async function getDirectVsRegularAudit(): Promise<DirectVsRegularAuditResponse> {
  const response = await fetch(`${API_BASE_URL}/audit/direct-vs-regular`);
  if (!response.ok) {
    throw new Error(`Direct-vs-regular audit failed: ${response.status}`);
  }
  return response.json() as Promise<DirectVsRegularAuditResponse>;
}

async function detailOrStatus(response: Response): Promise<string> {
  try {
    const body = (await response.json()) as { detail?: string };
    if (body.detail) return body.detail;
  } catch {
    // response body wasn't JSON -- fall through to the status code
  }
  return `HTTP ${response.status}`;
}

type IngestResponse =
  operations["ingest_cas_ingest_cas_post"]["responses"][200]["content"]["application/json"];

export async function postIngestCas(file: File, password: string): Promise<IngestResponse> {
  const formData = new FormData();
  formData.append("file", file);
  formData.append("password", password);
  const response = await fetch(`${API_BASE_URL}/ingest/cas`, { method: "POST", body: formData });
  if (!response.ok) {
    throw new Error(`Failed to ingest CAS: ${await detailOrStatus(response)}`);
  }
  return response.json() as Promise<IngestResponse>;
}

export async function postIngestCsv(file: File): Promise<IngestResponse> {
  const formData = new FormData();
  formData.append("file", file);
  const response = await fetch(`${API_BASE_URL}/ingest/csv`, { method: "POST", body: formData });
  if (!response.ok) {
    throw new Error(`Failed to ingest CSV: ${await detailOrStatus(response)}`);
  }
  return response.json() as Promise<IngestResponse>;
}

type RebuildResponse =
  operations["rebuild_lots_portfolio_rebuild_lots_post"]["responses"][200]["content"]["application/json"];

export async function postRebuildLots(): Promise<RebuildResponse> {
  const response = await fetch(`${API_BASE_URL}/portfolio/rebuild-lots`, { method: "POST" });
  if (!response.ok) {
    throw new Error(`Failed to rebuild lots: ${await detailOrStatus(response)}`);
  }
  return response.json() as Promise<RebuildResponse>;
}

export type DezervPreview = components["schemas"]["DezervPreviewResponse"];
export type DezervSchemePreview = components["schemas"]["DezervSchemePreviewResponse"];
export type DezervMatchCandidate = components["schemas"]["MatchCandidateResponse"];
export type DezervCommitResult = components["schemas"]["DezervCommitResponse"];

export async function postDezervPreview(file: File): Promise<DezervPreview> {
  const formData = new FormData();
  formData.append("file", file);
  const response = await fetch(`${API_BASE_URL}/ingest/dezerv/preview`, {
    method: "POST",
    body: formData,
  });
  if (!response.ok) {
    throw new Error(`Dezerv preview failed: ${await detailOrStatus(response)}`);
  }
  return response.json() as Promise<DezervPreview>;
}

export async function postDezervCommit(
  file: File,
  mapping: Record<string, string | null>,
): Promise<DezervCommitResult> {
  const formData = new FormData();
  formData.append("file", file);
  formData.append("mapping", JSON.stringify(mapping));
  const response = await fetch(`${API_BASE_URL}/ingest/dezerv/commit`, {
    method: "POST",
    body: formData,
  });
  if (!response.ok) {
    throw new Error(`Dezerv commit failed: ${await detailOrStatus(response)}`);
  }
  return response.json() as Promise<DezervCommitResult>;
}

export type ZerodhaPreview = components["schemas"]["ZerodhaPreviewResponse"];
export type ZerodhaSchemePreview = components["schemas"]["ZerodhaSchemePreviewResponse"];
export type ZerodhaCommitResult = components["schemas"]["ZerodhaCommitResponse"];

export async function postZerodhaPreview(file: File): Promise<ZerodhaPreview> {
  const formData = new FormData();
  formData.append("file", file);
  const response = await fetch(`${API_BASE_URL}/ingest/zerodha/preview`, {
    method: "POST",
    body: formData,
  });
  if (!response.ok) {
    throw new Error(`Zerodha preview failed: ${await detailOrStatus(response)}`);
  }
  return response.json() as Promise<ZerodhaPreview>;
}

export async function postZerodhaCommit(
  file: File,
  mapping: Record<string, string | null>,
): Promise<ZerodhaCommitResult> {
  const formData = new FormData();
  formData.append("file", file);
  formData.append("mapping", JSON.stringify(mapping));
  const response = await fetch(`${API_BASE_URL}/ingest/zerodha/commit`, {
    method: "POST",
    body: formData,
  });
  if (!response.ok) {
    throw new Error(`Zerodha commit failed: ${await detailOrStatus(response)}`);
  }
  return response.json() as Promise<ZerodhaCommitResult>;
}

export type Holding = components["schemas"]["HoldingResponse"];
type HoldingsResponse =
  operations["holdings_portfolio_holdings_get"]["responses"][200]["content"]["application/json"];

export async function getHoldings(): Promise<HoldingsResponse> {
  const response = await fetch(`${API_BASE_URL}/portfolio/holdings`);
  if (!response.ok) {
    throw new Error(`Holdings failed: ${response.status}`);
  }
  return response.json() as Promise<HoldingsResponse>;
}

type HoldingMetricsResponse =
  operations["holding_metrics_portfolio_metrics_get"]["responses"][200]["content"]["application/json"];

export async function getHoldingMetrics(): Promise<HoldingMetricsResponse> {
  const response = await fetch(`${API_BASE_URL}/portfolio/metrics`);
  if (!response.ok) {
    throw new Error(`Holding metrics failed: ${response.status}`);
  }
  return response.json() as Promise<HoldingMetricsResponse>;
}

export type FundDetail =
  operations["fund_detail_funds__amfi_code__get"]["responses"][200]["content"]["application/json"];

export async function getFundDetail(amfiCode: string): Promise<FundDetail> {
  const response = await fetch(`${API_BASE_URL}/funds/${encodeURIComponent(amfiCode)}`);
  if (!response.ok) {
    throw new Error(`Fund detail failed: ${await detailOrStatus(response)}`);
  }
  return response.json() as Promise<FundDetail>;
}

type TRIImportResponse =
  operations["import_tri_benchmarks_import_tri_post"]["responses"][200]["content"]["application/json"];

export async function postImportTri(benchmarkId: string, file: File): Promise<TRIImportResponse> {
  const formData = new FormData();
  formData.append("file", file);
  formData.append("benchmark_id", benchmarkId);
  const response = await fetch(`${API_BASE_URL}/benchmarks/import-tri`, {
    method: "POST",
    body: formData,
  });
  if (!response.ok) {
    throw new Error(`Failed to import TRI data: ${await detailOrStatus(response)}`);
  }
  return response.json() as Promise<TRIImportResponse>;
}

export type NavRefreshResponse =
  operations["refresh_market_data_refresh_post"]["responses"][200]["content"]["application/json"];

export async function postRefreshNav(): Promise<NavRefreshResponse> {
  const response = await fetch(`${API_BASE_URL}/market-data/refresh`, { method: "POST" });
  if (!response.ok) {
    throw new Error(`Failed to refresh NAV data: ${await detailOrStatus(response)}`);
  }
  return response.json() as Promise<NavRefreshResponse>;
}

type GrowwRefreshResponse =
  operations["refresh_groww_market_data_refresh_groww_post"]["responses"][200]["content"]["application/json"];

export async function postRefreshGroww(): Promise<GrowwRefreshResponse> {
  const response = await fetch(`${API_BASE_URL}/market-data/refresh-groww`, { method: "POST" });
  if (!response.ok) {
    throw new Error(`Failed to refresh fund metadata: ${await detailOrStatus(response)}`);
  }
  return response.json() as Promise<GrowwRefreshResponse>;
}

type BenchmarkRefreshResponse =
  operations["refresh_benchmarks_refresh_post"]["responses"][200]["content"]["application/json"];

export async function postRefreshBenchmarks(): Promise<BenchmarkRefreshResponse> {
  const response = await fetch(`${API_BASE_URL}/benchmarks/refresh`, { method: "POST" });
  if (!response.ok) {
    throw new Error(`Failed to refresh benchmark data: ${await detailOrStatus(response)}`);
  }
  return response.json() as Promise<BenchmarkRefreshResponse>;
}

type AiAnalysisResponse =
  operations["analyze_ai_analyze_post"]["responses"][200]["content"]["application/json"];

export async function postAiAnalyze(): Promise<AiAnalysisResponse> {
  const response = await fetch(`${API_BASE_URL}/ai/analyze`, { method: "POST" });
  if (!response.ok) {
    throw new Error(`AI analysis failed: ${await detailOrStatus(response)}`);
  }
  return response.json() as Promise<AiAnalysisResponse>;
}

export async function getLatestAiAnalysis(): Promise<AiAnalysisResponse | null> {
  const response = await fetch(`${API_BASE_URL}/ai/latest`);
  if (response.status === 404) {
    return null; // no analysis has been run yet -- an expected state, not an error
  }
  if (!response.ok) {
    throw new Error(`Failed to load latest AI analysis: ${await detailOrStatus(response)}`);
  }
  return response.json() as Promise<AiAnalysisResponse>;
}

export type Goal = components["schemas"]["GoalResponse"];
export type GoalHoldingRef = components["schemas"]["GoalHoldingRef"];
export type GoalProjection = components["schemas"]["GoalProjectionResponse"];

export async function getGoals(): Promise<Goal[]> {
  const response = await fetch(`${API_BASE_URL}/goals`);
  if (!response.ok) {
    throw new Error(`Failed to load goals: ${await detailOrStatus(response)}`);
  }
  return response.json() as Promise<Goal[]>;
}

export async function createGoal(
  name: string,
  targetAmountRupees: string,
  targetDate: string,
  inflationPctOverride?: string | null,
  includeInRetirementProjection = true,
): Promise<Goal> {
  const response = await fetch(`${API_BASE_URL}/goals`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      name,
      target_amount_rupees: targetAmountRupees,
      target_date: targetDate,
      inflation_pct_override: inflationPctOverride ?? null,
      include_in_retirement_projection: includeInRetirementProjection,
    }),
  });
  if (!response.ok) {
    throw new Error(`Failed to create goal: ${await detailOrStatus(response)}`);
  }
  return response.json() as Promise<Goal>;
}

export async function deleteGoal(goalId: string): Promise<void> {
  const response = await fetch(`${API_BASE_URL}/goals/${encodeURIComponent(goalId)}`, {
    method: "DELETE",
  });
  if (!response.ok) {
    throw new Error(`Failed to delete goal: ${await detailOrStatus(response)}`);
  }
}

export async function setGoalHoldings(
  goalId: string,
  holdings: GoalHoldingRef[],
): Promise<GoalHoldingRef[]> {
  const response = await fetch(`${API_BASE_URL}/goals/${encodeURIComponent(goalId)}/holdings`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(holdings),
  });
  if (!response.ok) {
    throw new Error(`Failed to tag goal holdings: ${await detailOrStatus(response)}`);
  }
  const body = (await response.json()) as { holdings: GoalHoldingRef[] };
  return body.holdings;
}

export async function getGoalHoldings(goalId: string): Promise<GoalHoldingRef[]> {
  const response = await fetch(`${API_BASE_URL}/goals/${encodeURIComponent(goalId)}/holdings`);
  if (!response.ok) {
    throw new Error(`Failed to load goal holdings: ${await detailOrStatus(response)}`);
  }
  const body = (await response.json()) as { holdings: GoalHoldingRef[] };
  return body.holdings;
}

export async function getGoalProjection(goalId: string): Promise<GoalProjection | null> {
  const response = await fetch(`${API_BASE_URL}/goals/${encodeURIComponent(goalId)}/projection`);
  if (response.status === 404) {
    return null; // no risk profile set yet -- an expected state, not an error
  }
  if (!response.ok) {
    throw new Error(`Failed to load goal projection: ${await detailOrStatus(response)}`);
  }
  return response.json() as Promise<GoalProjection>;
}

export type GoalMonteCarloResult = components["schemas"]["MonteCarloResultResponse"];

export async function getGoalMonteCarlo(goalId: string): Promise<GoalMonteCarloResult | null> {
  const response = await fetch(`${API_BASE_URL}/goals/${encodeURIComponent(goalId)}/monte-carlo`);
  if (response.status === 404) {
    return null; // no risk profile set yet -- an expected state, not an error
  }
  if (!response.ok) {
    throw new Error(`Failed to run Monte Carlo simulation: ${await detailOrStatus(response)}`);
  }
  return response.json() as Promise<GoalMonteCarloResult>;
}

export type YearlyReport = components["schemas"]["YearlyReportResponse"];

export async function getYearlyReport(fy: string): Promise<YearlyReport> {
  const response = await fetch(`${API_BASE_URL}/reports/yearly.json?fy=${encodeURIComponent(fy)}`);
  if (!response.ok) {
    throw new Error(`Failed to load yearly report: ${await detailOrStatus(response)}`);
  }
  return response.json() as Promise<YearlyReport>;
}

export function yearlyReportPdfUrl(fy: string): string {
  return `${API_BASE_URL}/reports/yearly?fy=${encodeURIComponent(fy)}`;
}

export function capitalGainsCsvUrl(fy: string): string {
  return `${API_BASE_URL}/reports/capital-gains.csv?fy=${encodeURIComponent(fy)}`;
}
