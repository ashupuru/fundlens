"""CSV export for the capital gains report (PRD FR-10.2).

Structured close to ITR's Schedule 112A shape (scheme-wise, per-transaction
gains) but not a literal field-for-field match: Schedule 112A specifically
covers LTCG on equity with FMV-as-on-31-Jan-2018 grandfathering, which is
explicitly P1 scope (compute/capital_gains.py's module docstring). This
export covers STCG and LTCG for both equity and debt, without the
grandfathered cost basis — labeled as a capital gains summary, not
represented as a ready-to-file Schedule 112A form.
"""

import csv
from io import StringIO

from fundlens.compute.capital_gains import CapitalGainsReport

_HEADER = [
    "Folio",
    "Scheme Name",
    "ISIN",
    "AMFI Code",
    "Asset Class",
    "Purchase Date",
    "Sale Date",
    "Holding Period (days)",
    "Units",
    "Sale Value (Rs.)",
    "Cost of Acquisition (Rs.)",
    "Gain (Rs.)",
    "Gain Type",
]


def _paise_to_rupees_str(paise: int) -> str:
    rupees = paise / 100
    return f"{rupees:.2f}"


def render_capital_gains_csv(report: CapitalGainsReport) -> str:
    buffer = StringIO()
    writer = csv.writer(buffer)
    writer.writerow(_HEADER)

    for line in report.lines:
        writer.writerow(
            [
                line.folio,
                line.scheme_name,
                line.isin or "",
                line.amfi_code or "",
                line.tax_asset_class.value if line.tax_asset_class else "unknown",
                line.purchase_date.isoformat(),
                line.sale_date.isoformat(),
                line.holding_period_days,
                str(line.units),
                _paise_to_rupees_str(line.sale_value_paise),
                _paise_to_rupees_str(line.cost_of_acquisition_paise),
                _paise_to_rupees_str(line.gain_paise),
                line.gain_type.value,
            ]
        )

    def _total_row(label: str, paise: int) -> list[str]:
        padding = [""] * (len(_HEADER) - 2)
        return [*padding, label, _paise_to_rupees_str(paise)]

    writer.writerow([])
    writer.writerow(_total_row("Total STCG", report.total_stcg_paise))
    writer.writerow(_total_row("Total LTCG", report.total_ltcg_paise))
    writer.writerow(_total_row("Total Unclassified", report.total_unclassified_paise))

    return buffer.getvalue()
