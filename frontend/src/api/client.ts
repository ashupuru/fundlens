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
