"""FR-10.1/FR-10.2: yearly report and capital gains report, both FY-aligned."""

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from fundlens.api.deps import get_session
from fundlens.compute.capital_gains import CapitalGainsReport, get_capital_gains_report
from fundlens.compute.yearly_report import YearlyReport, compute_yearly_report
from fundlens.reports.capital_gains_csv import render_capital_gains_csv
from fundlens.reports.yearly_report_html import render_yearly_report_html, render_yearly_report_pdf

router = APIRouter(prefix="/reports", tags=["reports"])


class CapitalGainsLineResponse(BaseModel):
    folio: str
    pan: str | None
    scheme_name: str
    isin: str | None
    amfi_code: str | None
    tax_asset_class: str | None
    purchase_date: str
    sale_date: str
    holding_period_days: int
    units: str
    sale_value_paise: int
    cost_of_acquisition_paise: int
    gain_paise: int
    gain_type: str
    grandfathering_applied: bool
    grandfathering_note: str | None
    ltcg_exemption_consumed_paise: int
    taxable_gain_paise: int
    exemption_note: str | None


class CapitalGainsReportResponse(BaseModel):
    financial_year: str
    lines: list[CapitalGainsLineResponse]
    total_stcg_paise: int
    total_ltcg_paise: int
    total_unclassified_paise: int
    total_taxable_gain_paise: int
    total_ltcg_exemption_used_paise: int
    schemes_missing_metadata: int


def _get_report_or_422(session: Session, fy: str) -> CapitalGainsReport:
    try:
        return get_capital_gains_report(session, fy)
    except (ValueError, IndexError) as exc:
        raise HTTPException(
            status_code=422, detail=f"Invalid financial year '{fy}', expected e.g. '2024-25'"
        ) from exc


def _capital_gains_to_response(report: CapitalGainsReport) -> CapitalGainsReportResponse:
    return CapitalGainsReportResponse(
        financial_year=report.financial_year,
        lines=[
            CapitalGainsLineResponse(
                folio=line.folio,
                pan=line.pan,
                scheme_name=line.scheme_name,
                isin=line.isin,
                amfi_code=line.amfi_code,
                tax_asset_class=line.tax_asset_class.value if line.tax_asset_class else None,
                purchase_date=line.purchase_date.isoformat(),
                sale_date=line.sale_date.isoformat(),
                holding_period_days=line.holding_period_days,
                units=str(line.units),
                sale_value_paise=line.sale_value_paise,
                cost_of_acquisition_paise=line.cost_of_acquisition_paise,
                gain_paise=line.gain_paise,
                gain_type=line.gain_type.value,
                grandfathering_applied=line.grandfathering_applied,
                grandfathering_note=line.grandfathering_note,
                ltcg_exemption_consumed_paise=line.ltcg_exemption_consumed_paise,
                taxable_gain_paise=line.taxable_gain_paise,
                exemption_note=line.exemption_note,
            )
            for line in report.lines
        ],
        total_stcg_paise=report.total_stcg_paise,
        total_ltcg_paise=report.total_ltcg_paise,
        total_unclassified_paise=report.total_unclassified_paise,
        total_taxable_gain_paise=report.total_taxable_gain_paise,
        total_ltcg_exemption_used_paise=report.total_ltcg_exemption_used_paise,
        schemes_missing_metadata=report.schemes_missing_metadata,
    )


@router.get("/capital-gains", response_model=CapitalGainsReportResponse)
def capital_gains(
    fy: str = Query(..., description="Financial year, e.g. '2024-25'"),
    session: Session = Depends(get_session),
) -> CapitalGainsReportResponse:
    return _capital_gains_to_response(_get_report_or_422(session, fy))


@router.get("/capital-gains.csv", response_class=PlainTextResponse)
def capital_gains_csv(
    fy: str = Query(..., description="Financial year, e.g. '2024-25'"),
    session: Session = Depends(get_session),
) -> PlainTextResponse:
    report = _get_report_or_422(session, fy)
    csv_text = render_capital_gains_csv(report)
    return PlainTextResponse(
        content=csv_text,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="capital_gains_{fy}.csv"'},
    )


class FundPerformanceResponse(BaseModel):
    folio: str
    scheme_key: str
    scheme_name: str
    amc: str
    xirr: float | None
    xirr_reason: str | None


class AllocationDriftSummaryResponse(BaseModel):
    persona: str
    target_equity_pct: float
    target_debt_pct: float
    actual_equity_pct: float | None
    actual_debt_pct: float | None


class YearlyReportResponse(BaseModel):
    financial_year: str
    fy_start: str
    report_end: str
    opening_value_paise: int
    closing_value_paise: int
    portfolio_xirr_this_year: float | None
    portfolio_xirr_this_year_reason: str | None
    portfolio_xirr_since_inception: float | None
    portfolio_xirr_since_inception_reason: str | None
    benchmark_id: str
    benchmark_xirr_since_inception: float | None
    benchmark_xirr_since_inception_reason: str | None
    alpha_pct_since_inception: float | None
    best_funds: list[FundPerformanceResponse]
    worst_funds: list[FundPerformanceResponse]
    allocation_drift: AllocationDriftSummaryResponse | None
    estimated_annual_fee_paise: int
    fee_funds_excluded: int
    capital_gains: CapitalGainsReportResponse


def _get_yearly_report_or_422(session: Session, fy: str) -> YearlyReport:
    try:
        return compute_yearly_report(session, fy)
    except (ValueError, IndexError) as exc:
        raise HTTPException(
            status_code=422, detail=f"Invalid financial year '{fy}', expected e.g. '2024-25'"
        ) from exc


def _yearly_report_to_response(report: YearlyReport) -> YearlyReportResponse:
    drift = report.allocation_drift
    return YearlyReportResponse(
        financial_year=report.financial_year,
        fy_start=report.fy_start.isoformat(),
        report_end=report.report_end.isoformat(),
        opening_value_paise=report.opening_value_paise,
        closing_value_paise=report.closing_value_paise,
        portfolio_xirr_this_year=report.portfolio_xirr_this_year,
        portfolio_xirr_this_year_reason=report.portfolio_xirr_this_year_reason,
        portfolio_xirr_since_inception=report.portfolio_xirr_since_inception,
        portfolio_xirr_since_inception_reason=report.portfolio_xirr_since_inception_reason,
        benchmark_id=report.benchmark_id.value,
        benchmark_xirr_since_inception=report.benchmark_xirr_since_inception,
        benchmark_xirr_since_inception_reason=report.benchmark_xirr_since_inception_reason,
        alpha_pct_since_inception=report.alpha_pct_since_inception,
        best_funds=[
            FundPerformanceResponse(
                folio=f.folio, scheme_key=f.scheme_key, scheme_name=f.scheme_name, amc=f.amc,
                xirr=f.xirr, xirr_reason=f.xirr_reason,
            )
            for f in report.best_funds
        ],
        worst_funds=[
            FundPerformanceResponse(
                folio=f.folio, scheme_key=f.scheme_key, scheme_name=f.scheme_name, amc=f.amc,
                xirr=f.xirr, xirr_reason=f.xirr_reason,
            )
            for f in report.worst_funds
        ],
        allocation_drift=(
            AllocationDriftSummaryResponse(
                persona=drift.persona.value,
                target_equity_pct=drift.target_equity_pct,
                target_debt_pct=drift.target_debt_pct,
                actual_equity_pct=drift.actual_equity_pct,
                actual_debt_pct=drift.actual_debt_pct,
            )
            if drift is not None
            else None
        ),
        estimated_annual_fee_paise=report.estimated_annual_fee_paise,
        fee_funds_excluded=report.fee_funds_excluded,
        capital_gains=_capital_gains_to_response(report.capital_gains),
    )


@router.get("/yearly.json", response_model=YearlyReportResponse)
def yearly_report_json(
    fy: str = Query(..., description="Financial year, e.g. '2024-25'"),
    session: Session = Depends(get_session),
) -> YearlyReportResponse:
    return _yearly_report_to_response(_get_yearly_report_or_422(session, fy))


@router.get("/yearly")
def yearly_report_pdf(
    fy: str = Query(..., description="Financial year, e.g. '2024-25'"),
    session: Session = Depends(get_session),
) -> Response:
    report = _get_yearly_report_or_422(session, fy)
    html = render_yearly_report_html(report)
    pdf_bytes = render_yearly_report_pdf(html)
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="yearly_report_{fy}.pdf"'},
    )
