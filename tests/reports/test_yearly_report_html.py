from datetime import date

from fundlens.compute.benchmark import BenchmarkId
from fundlens.compute.capital_gains import CapitalGainsReport
from fundlens.compute.yearly_report import FundPerformance, YearlyReport
from fundlens.reports.yearly_report_html import render_yearly_report_html, render_yearly_report_pdf


def _make_report(**overrides: object) -> YearlyReport:
    defaults: dict[str, object] = dict(
        financial_year="2023-24",
        fy_start=date(2023, 4, 1),
        report_end=date(2024, 3, 31),
        opening_value_paise=1_500_000,
        closing_value_paise=1_800_000,
        portfolio_xirr_this_year=0.20,
        portfolio_xirr_this_year_reason=None,
        portfolio_xirr_since_inception=0.15,
        portfolio_xirr_since_inception_reason=None,
        benchmark_id=BenchmarkId.NIFTY50_TRI,
        benchmark_xirr_since_inception=0.12,
        benchmark_xirr_since_inception_reason=None,
        alpha_pct_since_inception=3.0,
        best_funds=[
            FundPerformance(
                folio="F1", scheme_key="ISIN111", scheme_name="Fund A", amc="AMC A",
                xirr=0.25, xirr_reason=None,
            )
        ],
        worst_funds=[
            FundPerformance(
                folio="F1", scheme_key="ISIN222", scheme_name="Fund B", amc="AMC B",
                xirr=-0.05, xirr_reason=None,
            )
        ],
        allocation_drift=None,
        estimated_annual_fee_paise=10_000,
        fee_funds_excluded=1,
        capital_gains=CapitalGainsReport("2023-24", [], 0, 0, 0, 0, 0, 0),
    )
    defaults.update(overrides)
    return YearlyReport(**defaults)  # type: ignore[arg-type]


def test_html_contains_key_figures() -> None:
    report = _make_report()

    html = render_yearly_report_html(report)

    assert "FY 2023-24" in html
    assert "20.00%" in html  # this year's XIRR
    assert "Fund A" in html
    assert "Fund B" in html
    assert "AMC A" in html


def test_html_shows_no_risk_profile_message_when_drift_is_none() -> None:
    report = _make_report(allocation_drift=None)

    html = render_yearly_report_html(report)

    assert "No risk profile set" in html


def test_html_renders_nav_reason_when_xirr_unavailable() -> None:
    report = _make_report(
        portfolio_xirr_this_year=None, portfolio_xirr_this_year_reason="nav_unavailable"
    )

    html = render_yearly_report_html(report)

    assert "n/a (nav_unavailable)" in html


def test_html_shows_no_ranking_message_when_no_funds() -> None:
    report = _make_report(best_funds=[], worst_funds=[])

    html = render_yearly_report_html(report)

    assert "Not enough distinct positions" in html


def test_render_pdf_produces_valid_pdf_bytes() -> None:
    html = render_yearly_report_html(_make_report())

    pdf_bytes = render_yearly_report_pdf(html)

    assert pdf_bytes[:4] == b"%PDF"
