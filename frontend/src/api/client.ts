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
  level: "category" | "sub_category",
): Promise<CategoryBreakdownResponse> {
  const response = await fetch(`${API_BASE_URL}/portfolio/breakdown?level=${level}`);
  if (!response.ok) {
    throw new Error(`Category breakdown failed: ${response.status}`);
  }
  return response.json() as Promise<CategoryBreakdownResponse>;
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

type GrowwRefreshResponse =
  operations["refresh_groww_market_data_refresh_groww_post"]["responses"][200]["content"]["application/json"];

export async function postRefreshGroww(): Promise<GrowwRefreshResponse> {
  const response = await fetch(`${API_BASE_URL}/market-data/refresh-groww`, { method: "POST" });
  if (!response.ok) {
    throw new Error(`Failed to refresh fund metadata: ${await detailOrStatus(response)}`);
  }
  return response.json() as Promise<GrowwRefreshResponse>;
}
