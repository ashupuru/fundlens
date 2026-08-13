"""FR-4.1/4.2: benchmark data refresh and fund-vs-benchmark XIRR comparison."""

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from fundlens.api.deps import get_session
from fundlens.benchmark.service import refresh_all_benchmarks
from fundlens.compute.benchmark import (
    compute_portfolio_benchmark_comparison,
    get_all_benchmark_comparisons,
)
from fundlens.db.models import BenchmarkId

router = APIRouter(prefix="/benchmarks", tags=["benchmarks"])


class BenchmarkRefreshResultResponse(BaseModel):
    benchmark_id: str
    inserted: int
    error: str | None


@router.post("/refresh", response_model=list[BenchmarkRefreshResultResponse])
def refresh(session: Session = Depends(get_session)) -> list[BenchmarkRefreshResultResponse]:
    return [
        BenchmarkRefreshResultResponse(
            benchmark_id=r.benchmark_id.value, inserted=r.inserted, error=r.error
        )
        for r in refresh_all_benchmarks(session)
    ]


class BenchmarkComparisonResponse(BaseModel):
    folio: str
    scheme_name: str
    amc: str
    benchmark_id: str | None
    proxy_note: str | None
    fund_xirr: float | None
    fund_xirr_reason: str | None
    benchmark_xirr: float | None
    benchmark_xirr_reason: str | None
    alpha_pct: float | None


@router.get("/comparison", response_model=list[BenchmarkComparisonResponse])
def comparison(session: Session = Depends(get_session)) -> list[BenchmarkComparisonResponse]:
    return [
        BenchmarkComparisonResponse(
            folio=c.folio,
            scheme_name=c.scheme_name,
            amc=c.amc,
            benchmark_id=c.benchmark_id.value if c.benchmark_id else None,
            proxy_note=c.proxy_note,
            fund_xirr=c.fund_xirr,
            fund_xirr_reason=c.fund_xirr_reason,
            benchmark_xirr=c.benchmark_xirr,
            benchmark_xirr_reason=c.benchmark_xirr_reason,
            alpha_pct=c.alpha_pct,
        )
        for c in get_all_benchmark_comparisons(session)
    ]


class PortfolioBenchmarkComparisonResponse(BaseModel):
    benchmark_id: str
    proxy_note: str
    portfolio_xirr: float | None
    portfolio_xirr_reason: str | None
    benchmark_xirr: float | None
    benchmark_xirr_reason: str | None
    alpha_pct: float | None


@router.get("/comparison/portfolio", response_model=PortfolioBenchmarkComparisonResponse)
def portfolio_comparison(
    benchmark_id: str = Query(
        default=BenchmarkId.NIFTY50_TRI.value,
        description=(
            "Reference index for portfolio-level comparison (PRD §12.3 default: Nifty 50 TRI)"
        ),
    ),
    session: Session = Depends(get_session),
) -> PortfolioBenchmarkComparisonResponse:
    try:
        resolved = BenchmarkId(benchmark_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=422, detail=f"Unknown benchmark_id '{benchmark_id}'"
        ) from exc

    c = compute_portfolio_benchmark_comparison(session, resolved)
    return PortfolioBenchmarkComparisonResponse(
        benchmark_id=c.benchmark_id.value,
        proxy_note=c.proxy_note,
        portfolio_xirr=c.portfolio_xirr,
        portfolio_xirr_reason=c.portfolio_xirr_reason,
        benchmark_xirr=c.benchmark_xirr,
        benchmark_xirr_reason=c.benchmark_xirr_reason,
        alpha_pct=c.alpha_pct,
    )
