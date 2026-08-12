"""FR-10.2: capital gains report, scheme-wise STCG/LTCG for a financial year."""

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from fundlens.api.deps import get_session
from fundlens.compute.capital_gains import CapitalGainsReport, get_capital_gains_report
from fundlens.reports.capital_gains_csv import render_capital_gains_csv

router = APIRouter(prefix="/reports", tags=["reports"])


class CapitalGainsLineResponse(BaseModel):
    folio: str
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


class CapitalGainsReportResponse(BaseModel):
    financial_year: str
    lines: list[CapitalGainsLineResponse]
    total_stcg_paise: int
    total_ltcg_paise: int
    total_unclassified_paise: int
    schemes_missing_metadata: int


def _get_report_or_422(session: Session, fy: str) -> CapitalGainsReport:
    try:
        return get_capital_gains_report(session, fy)
    except (ValueError, IndexError) as exc:
        raise HTTPException(
            status_code=422, detail=f"Invalid financial year '{fy}', expected e.g. '2024-25'"
        ) from exc


@router.get("/capital-gains", response_model=CapitalGainsReportResponse)
def capital_gains(
    fy: str = Query(..., description="Financial year, e.g. '2024-25'"),
    session: Session = Depends(get_session),
) -> CapitalGainsReportResponse:
    report = _get_report_or_422(session, fy)
    return CapitalGainsReportResponse(
        financial_year=report.financial_year,
        lines=[
            CapitalGainsLineResponse(
                folio=line.folio,
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
            )
            for line in report.lines
        ],
        total_stcg_paise=report.total_stcg_paise,
        total_ltcg_paise=report.total_ltcg_paise,
        total_unclassified_paise=report.total_unclassified_paise,
        schemes_missing_metadata=report.schemes_missing_metadata,
    )


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
