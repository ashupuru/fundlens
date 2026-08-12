import csv
from datetime import date
from decimal import Decimal
from io import StringIO

from fundlens.compute.capital_gains import CapitalGainsLine, CapitalGainsReport
from fundlens.reports.capital_gains_csv import render_capital_gains_csv
from fundlens.tax.asset_class import TaxAssetClass
from fundlens.tax.classification import GainType


def _make_line(**overrides: object) -> CapitalGainsLine:
    defaults: dict[str, object] = dict(
        folio="F1",
        scheme_key="ISIN111",
        scheme_name="Fund A",
        isin="ISIN111",
        amfi_code="111",
        purchase_date=date(2022, 1, 1),
        sale_date=date(2024, 6, 1),
        units=Decimal("100.5"),
        sale_value_paise=800_000,
        cost_of_acquisition_paise=500_000,
        gain_paise=300_000,
        holding_period_days=882,
        gain_type=GainType.LTCG,
        tax_asset_class=TaxAssetClass.EQUITY,
    )
    defaults.update(overrides)
    return CapitalGainsLine(**defaults)  # type: ignore[arg-type]


def test_csv_has_header_and_one_data_row() -> None:
    report = CapitalGainsReport(
        financial_year="2024-25",
        lines=[_make_line()],
        total_stcg_paise=0,
        total_ltcg_paise=300_000,
        total_unclassified_paise=0,
        schemes_missing_metadata=0,
    )

    csv_text = render_capital_gains_csv(report)
    rows = list(csv.reader(StringIO(csv_text)))

    header = rows[0]
    assert "Scheme Name" in header
    assert "Gain Type" in header

    data_row = rows[1]
    assert data_row[header.index("Scheme Name")] == "Fund A"
    assert data_row[header.index("Sale Value (Rs.)")] == "8000.00"
    assert data_row[header.index("Gain Type")] == "ltcg"


def test_csv_rupee_conversion_from_paise_is_correct() -> None:
    report = CapitalGainsReport(
        financial_year="2024-25",
        lines=[
            _make_line(
                sale_value_paise=123_456, cost_of_acquisition_paise=100_000, gain_paise=23_456
            )
        ],
        total_stcg_paise=0,
        total_ltcg_paise=23_456,
        total_unclassified_paise=0,
        schemes_missing_metadata=0,
    )

    csv_text = render_capital_gains_csv(report)
    rows = list(csv.reader(StringIO(csv_text)))
    header, data_row = rows[0], rows[1]

    assert data_row[header.index("Sale Value (Rs.)")] == "1234.56"
    assert data_row[header.index("Cost of Acquisition (Rs.)")] == "1000.00"
    assert data_row[header.index("Gain (Rs.)")] == "234.56"


def test_csv_includes_totals_rows() -> None:
    report = CapitalGainsReport(
        financial_year="2024-25",
        lines=[_make_line()],
        total_stcg_paise=50_000,
        total_ltcg_paise=300_000,
        total_unclassified_paise=10_000,
        schemes_missing_metadata=1,
    )

    csv_text = render_capital_gains_csv(report)

    assert "Total STCG" in csv_text
    assert "500.00" in csv_text  # 50,000 paise
    assert "Total LTCG" in csv_text
    assert "3000.00" in csv_text  # 300,000 paise
    assert "Total Unclassified" in csv_text


def test_csv_handles_missing_isin_and_amfi_code_gracefully() -> None:
    report = CapitalGainsReport(
        financial_year="2024-25",
        lines=[
            _make_line(
                isin=None, amfi_code=None, tax_asset_class=None, gain_type=GainType.UNCLASSIFIED
            )
        ],
        total_stcg_paise=0,
        total_ltcg_paise=0,
        total_unclassified_paise=300_000,
        schemes_missing_metadata=1,
    )

    csv_text = render_capital_gains_csv(report)
    rows = list(csv.reader(StringIO(csv_text)))
    header, data_row = rows[0], rows[1]

    assert data_row[header.index("ISIN")] == ""
    assert data_row[header.index("AMFI Code")] == ""
    assert data_row[header.index("Asset Class")] == "unknown"


def test_empty_report_produces_header_and_totals_only() -> None:
    report = CapitalGainsReport(
        financial_year="2024-25",
        lines=[],
        total_stcg_paise=0,
        total_ltcg_paise=0,
        total_unclassified_paise=0,
        schemes_missing_metadata=0,
    )

    csv_text = render_capital_gains_csv(report)
    rows = [r for r in csv.reader(StringIO(csv_text)) if r]

    assert len(rows) == 4  # header + 3 total rows, no data rows
