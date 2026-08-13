import type { operations } from "./schema";

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
    const body: unknown = await response.json();
    if (body && typeof body === "object" && "detail" in body) {
      const detail = (body as { detail: unknown }).detail;
      if (typeof detail === "string") return detail;
    }
  } catch {
    // response body wasn't JSON — fall through to the status-based message
  }
  return `request failed: ${response.status}`;
}

export interface DezervMatchCandidate {
  amfi_code: string;
  scheme_name: string;
  isin: string | null;
  score: number;
}

export interface DezervSchemePreview {
  security_name: string;
  symbol_code: string;
  txn_count: number;
  matched_amfi_code: string | null;
  matched_scheme_name: string | null;
  matched_isin: string | null;
  match_score: number | null;
  reason: string | null;
  suggestions: DezervMatchCandidate[];
}

export interface DezervPreview {
  schemes: DezervSchemePreview[];
  unrecognized_transaction_descriptions: string[];
  total_mfdemat_rows: number;
  date_range_start: string | null;
  date_range_end: string | null;
}

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

export interface DezervCommitResult {
  upload_id: string;
  inserted_count: number;
  duplicate_count: number;
  skipped_count: number;
  skipped_reasons: Record<string, number>;
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

export interface SchemeRebuildError {
  folio: string;
  scheme_key: string;
  message: string;
}

export interface RebuildLotsResult {
  folios_processed: number;
  schemes_processed: number;
  lots_created: number;
  realized_gains_created: number;
  errors: SchemeRebuildError[];
}

export async function postRebuildLots(): Promise<RebuildLotsResult> {
  const response = await fetch(`${API_BASE_URL}/portfolio/rebuild-lots`, { method: "POST" });
  if (!response.ok) {
    throw new Error(`Rebuild lots failed: ${await detailOrStatus(response)}`);
  }
  return response.json() as Promise<RebuildLotsResult>;
}
