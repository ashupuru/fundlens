"""PortfolioMetricsSnapshot: the sole input contract into the AI layer (PRD
FR-5.x, docs/architecture/ai-layer.md). An explicit allowlist serializer --
never a generic ORM dump -- composed entirely from existing compute
functions. No `folio`/`pan`/`amc` field anywhere in this model tree: the
sensitive surface for this app is account identity, not fund identity, so
fund *names* are included deliberately (the AI layer needs to name funds in
its narrative).

`overlap_pairs` and `style_drift_flags` are always `[]` -- both are
documented, real gaps (fund overlap needs AMC portfolio disclosures, a data
source this app doesn't ingest; style drift needs a historical cap-mix
series, which isn't persisted anywhere), never fabricated to fill the field.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from fundlens.allocation.service import get_risk_profile
from fundlens.compute.allocation import compute_allocation_drift
from fundlens.compute.benchmark import (
    compute_portfolio_benchmark_comparison,
    get_all_benchmark_comparisons,
)
from fundlens.compute.holdings import get_current_holdings
from fundlens.compute.metrics import compute_portfolio_metrics, get_all_holding_metrics
from fundlens.compute.verdict import get_all_verdicts
from fundlens.db.models import BenchmarkId, GrowwFundMetadata


class AllocationBreakdown(BaseModel):
    target_equity_pct: float
    target_debt_pct: float
    actual_equity_pct: float | None
    actual_debt_pct: float | None
    actual_other_pct: float | None
    equity_drift_pct: float | None
    debt_drift_pct: float | None


class FundMetrics(BaseModel):
    scheme_name: str
    category: str | None
    xirr: float | None
    alpha_pct: float | None
    expense_ratio: float | None
    plan_type: str | None
    verdict: str | None
    verdict_score: float | None
    aum_crores: float | None
    groww_rating: int | None


class OverlapPair(BaseModel):
    fund_a: str
    fund_b: str
    overlap_pct: float


class ConcentrationStats(BaseModel):
    top_1_pct: float
    top_3_pct: float
    top_5_pct: float
    hhi: float


class PortfolioMetricsSnapshot(BaseModel):
    as_of_date: date
    portfolio_xirr: float | None
    portfolio_xirr_reason: str | None
    benchmark_xirr: float | None
    benchmark_name: str | None
    alpha_pct: float | None
    allocation: AllocationBreakdown | None
    funds: list[FundMetrics]
    overlap_pairs: list[OverlapPair]
    fee_drag_annual_rupees: float | None
    fee_drag_funds_excluded: int
    concentration: ConcentrationStats
    style_drift_flags: list[str]


@dataclass(frozen=True)
class _ConcentrationAndFeeDrag:
    concentration: ConcentrationStats
    fee_drag_annual_rupees: float | None
    fee_drag_funds_excluded: int


def _compute_concentration_and_fee_drag(
    values_paise: list[int], expense_ratios: list[Decimal | None]
) -> _ConcentrationAndFeeDrag:
    total = sum(values_paise)
    if total <= 0:
        return _ConcentrationAndFeeDrag(
            concentration=ConcentrationStats(top_1_pct=0.0, top_3_pct=0.0, top_5_pct=0.0, hhi=0.0),
            fee_drag_annual_rupees=None,
            fee_drag_funds_excluded=len(values_paise),
        )

    sorted_values = sorted(values_paise, reverse=True)

    def top_n_pct(n: int) -> float:
        return sum(sorted_values[:n]) / total * 100

    hhi = sum((v / total * 100) ** 2 for v in sorted_values)
    concentration = ConcentrationStats(
        top_1_pct=top_n_pct(1), top_3_pct=top_n_pct(3), top_5_pct=top_n_pct(5), hhi=hhi
    )

    fee_drag_paise = Decimal(0)
    excluded = 0
    for value_paise, expense_ratio in zip(values_paise, expense_ratios, strict=True):
        if expense_ratio is None:
            excluded += 1
            continue
        fee_drag_paise += Decimal(value_paise) * expense_ratio / 100

    fee_drag_rupees = float(fee_drag_paise / 100) if excluded < len(values_paise) else None
    return _ConcentrationAndFeeDrag(
        concentration=concentration,
        fee_drag_annual_rupees=fee_drag_rupees,
        fee_drag_funds_excluded=excluded,
    )


def build_portfolio_snapshot(session: Session) -> PortfolioMetricsSnapshot:
    portfolio_metrics = compute_portfolio_metrics(session)
    benchmark_comparison = compute_portfolio_benchmark_comparison(session, BenchmarkId.NIFTY50_TRI)

    profile = get_risk_profile(session)
    allocation = None
    if profile is not None:
        drift = compute_allocation_drift(session, profile)
        allocation = AllocationBreakdown(
            target_equity_pct=drift.target_equity_pct,
            target_debt_pct=drift.target_debt_pct,
            actual_equity_pct=drift.actual_equity_pct,
            actual_debt_pct=drift.actual_debt_pct,
            actual_other_pct=drift.actual_other_pct,
            equity_drift_pct=drift.equity_drift_pct,
            debt_drift_pct=drift.debt_drift_pct,
        )

    amfi_code_by_key = {
        (h.folio, h.scheme_key): h.amfi_code for h in get_current_holdings(session)
    }
    alpha_by_key = {
        (c.folio, c.scheme_key): c.alpha_pct for c in get_all_benchmark_comparisons(session)
    }
    verdict_by_key = {(v.folio, v.scheme_key): v for v in get_all_verdicts(session)}

    open_holdings = [h for h in get_all_holding_metrics(session) if not h.is_fully_exited]
    amfi_codes = {
        amfi_code_by_key.get((h.folio, h.scheme_key))
        for h in open_holdings
        if amfi_code_by_key.get((h.folio, h.scheme_key))
    }
    groww_by_code = {
        m.amfi_code: m
        for m in session.scalars(
            select(GrowwFundMetadata).where(GrowwFundMetadata.amfi_code.in_(amfi_codes))
        )
    }

    funds: list[FundMetrics] = []
    values_paise: list[int] = []
    expense_ratios: list[Decimal | None] = []
    for h in open_holdings:
        key = (h.folio, h.scheme_key)
        amfi_code = amfi_code_by_key.get(key)
        groww = groww_by_code.get(amfi_code) if amfi_code else None
        verdict = verdict_by_key.get(key)

        funds.append(
            FundMetrics(
                scheme_name=h.scheme_name,
                category=groww.category if groww else None,
                xirr=h.xirr,
                alpha_pct=alpha_by_key.get(key),
                expense_ratio=float(groww.expense_ratio) if groww and groww.expense_ratio else None,
                plan_type=groww.plan_type if groww else None,
                verdict=verdict.verdict_type.value if verdict else None,
                verdict_score=verdict.overall_score if verdict else None,
                aum_crores=float(groww.aum) if groww and groww.aum is not None else None,
                groww_rating=groww.groww_rating if groww else None,
            )
        )
        value_paise = h.current_value_paise or 0
        values_paise.append(value_paise)
        expense_ratios.append(groww.expense_ratio if groww else None)

    stats = _compute_concentration_and_fee_drag(values_paise, expense_ratios)

    return PortfolioMetricsSnapshot(
        as_of_date=date.today(),
        portfolio_xirr=portfolio_metrics.xirr,
        portfolio_xirr_reason=portfolio_metrics.xirr_reason,
        benchmark_xirr=benchmark_comparison.benchmark_xirr,
        benchmark_name=benchmark_comparison.benchmark_id.value,
        alpha_pct=benchmark_comparison.alpha_pct,
        allocation=allocation,
        funds=funds,
        overlap_pairs=[],
        fee_drag_annual_rupees=stats.fee_drag_annual_rupees,
        fee_drag_funds_excluded=stats.fee_drag_funds_excluded,
        concentration=stats.concentration,
        style_drift_flags=[],
    )
