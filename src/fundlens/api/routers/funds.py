"""GET /funds/{amfi_code}: a Groww-style fund detail page backend — fund
metadata (AUM/rating/expense-ratio/returns, best-effort from Groww;
category/tax-class from AMFI), the user's own position aggregated across
every folio that holds it (compute/metrics.py::compute_fund_metrics), and
similar funds for comparison (same sub_category, ranked by rating then
AUM, market_data/groww_service.py-populated)."""

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from fundlens.api.deps import get_session
from fundlens.compute import metrics
from fundlens.compute.holdings import get_holdings_for_scheme
from fundlens.compute.rolling_returns import RollingReturnWindow, compute_rolling_returns
from fundlens.db.models import GrowwFundMetadata, SchemeMetadata

router = APIRouter(prefix="/funds", tags=["funds"])

_SIMILAR_FUNDS_LIMIT = 5


class FundHoldingResponse(BaseModel):
    folio: str
    units_remaining: str
    cost_basis_paise: int
    average_cost_per_unit: str


class FundPositionResponse(BaseModel):
    xirr: float | None
    xirr_reason: str | None
    invested_paise: int
    received_paise: int
    current_value_paise: int
    positions_missing_nav: int


class SimilarFundResponse(BaseModel):
    amfi_code: str
    scheme_name: str
    groww_rating: int | None
    aum: str | None
    return_3y: str | None


class RollingWindowSampleResponse(BaseModel):
    as_of_date: str
    alpha_pct: float | None


class RollingReturnWindowResponse(BaseModel):
    years: int
    mean_alpha_pct: float | None
    consistency_pct: float | None
    sample_count: int
    reason: str | None
    samples: list[RollingWindowSampleResponse]


class FundDetailResponse(BaseModel):
    amfi_code: str
    scheme_name: str | None
    fund_house: str | None
    category: str | None
    sub_category: str | None
    plan_type: str | None
    aum: str | None
    groww_rating: int | None
    risk_rating: int | None
    expense_ratio: str | None
    fund_manager: str | None
    exit_load: str | None
    return_1y: str | None
    return_3y: str | None
    return_5y: str | None
    return_10y: str | None
    tax_asset_class: str | None
    metadata_available: bool  # False if Groww has no listing for this scheme
    your_position: FundPositionResponse | None
    holdings: list[FundHoldingResponse]
    similar_funds: list[SimilarFundResponse]
    rolling_returns: list[RollingReturnWindowResponse]


@router.get("/{amfi_code}", response_model=FundDetailResponse)
def fund_detail(amfi_code: str, session: Session = Depends(get_session)) -> FundDetailResponse:
    groww = session.get(GrowwFundMetadata, amfi_code)
    scheme_meta = session.get(SchemeMetadata, amfi_code)

    fund_holdings = get_holdings_for_scheme(session, amfi_code)
    scheme_name = groww.scheme_name if groww else None
    if scheme_name is None and fund_holdings:
        scheme_name = fund_holdings[0].scheme_name  # Groww has no listing; fall back to our own

    position = metrics.compute_fund_metrics(session, amfi_code)
    your_position = (
        FundPositionResponse(
            xirr=position.xirr,
            xirr_reason=position.xirr_reason,
            invested_paise=position.invested_paise,
            received_paise=position.received_paise,
            current_value_paise=position.current_value_paise,
            positions_missing_nav=position.positions_missing_nav,
        )
        if position is not None
        else None
    )

    holdings = [
        FundHoldingResponse(
            folio=h.folio,
            units_remaining=str(h.units_remaining),
            cost_basis_paise=h.cost_basis_paise,
            average_cost_per_unit=str(h.average_cost_per_unit),
        )
        for h in fund_holdings
    ]

    rolling_windows: list[RollingReturnWindow] = []
    if scheme_meta is not None:
        rolling_windows = compute_rolling_returns(
            session, amfi_code, scheme_meta.category_raw, scheme_meta.tax_asset_class
        )
    rolling_returns = [
        RollingReturnWindowResponse(
            years=w.years,
            mean_alpha_pct=w.mean_alpha_pct,
            consistency_pct=w.consistency_pct,
            sample_count=len(w.samples),
            reason=w.reason,
            samples=[
                RollingWindowSampleResponse(
                    as_of_date=s.as_of_date.isoformat(), alpha_pct=s.alpha_pct
                )
                for s in w.samples
            ],
        )
        for w in rolling_windows
    ]

    similar_funds: list[SimilarFundResponse] = []
    if groww is not None and groww.sub_category:
        rows = session.scalars(
            select(GrowwFundMetadata)
            .where(
                GrowwFundMetadata.sub_category == groww.sub_category,
                GrowwFundMetadata.amfi_code != amfi_code,
            )
            .order_by(
                GrowwFundMetadata.groww_rating.desc().nulls_last(),
                GrowwFundMetadata.aum.desc().nulls_last(),
            )
            .limit(_SIMILAR_FUNDS_LIMIT)
        ).all()
        similar_funds = [
            SimilarFundResponse(
                amfi_code=r.amfi_code,
                scheme_name=r.scheme_name,
                groww_rating=r.groww_rating,
                aum=str(r.aum) if r.aum is not None else None,
                return_3y=str(r.return_3y) if r.return_3y is not None else None,
            )
            for r in rows
        ]

    return FundDetailResponse(
        amfi_code=amfi_code,
        scheme_name=scheme_name,
        fund_house=groww.fund_house if groww else None,
        category=groww.category if groww else None,
        sub_category=groww.sub_category if groww else None,
        plan_type=groww.plan_type if groww else None,
        aum=str(groww.aum) if groww and groww.aum is not None else None,
        groww_rating=groww.groww_rating if groww else None,
        risk_rating=groww.risk_rating if groww else None,
        expense_ratio=(
            str(groww.expense_ratio) if groww and groww.expense_ratio is not None else None
        ),
        fund_manager=groww.fund_manager if groww else None,
        exit_load=groww.exit_load if groww else None,
        return_1y=str(groww.return_1y) if groww and groww.return_1y is not None else None,
        return_3y=str(groww.return_3y) if groww and groww.return_3y is not None else None,
        return_5y=str(groww.return_5y) if groww and groww.return_5y is not None else None,
        return_10y=str(groww.return_10y) if groww and groww.return_10y is not None else None,
        tax_asset_class=scheme_meta.tax_asset_class.value if scheme_meta else None,
        metadata_available=groww is not None,
        your_position=your_position,
        holdings=holdings,
        similar_funds=similar_funds,
        rolling_returns=rolling_returns,
    )
