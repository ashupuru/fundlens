"""Capital gains report (PRD FR-10.2): scheme-wise STCG/LTCG for a
financial year, sourced directly from the FIFO realized-gain ledger
(compute/lots.py) — never recomputed independently, so the report can never
drift from what the lot allocator actually determined.

FY-aligned (Apr-Mar, PRD §10.1). Minimal P0.5 scope, per the dev plan: no
grandfathering, no ₹1.25L/FY exemption tracking (both P1) — raw classified
gains only, with any scheme lacking usable classification explicitly
reported as unclassified rather than silently taxed either way.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from fundlens.db.models import RawTransaction, RealizedGain, SchemeMetadata
from fundlens.money import to_paise_strict
from fundlens.tax.asset_class import TaxAssetClass
from fundlens.tax.classification import GainClassification, GainType, classify_gain


@dataclass(frozen=True)
class CapitalGainsLine:
    folio: str
    scheme_key: str
    scheme_name: str
    isin: str | None
    amfi_code: str | None
    purchase_date: date
    sale_date: date
    units: Decimal
    sale_value_paise: int
    cost_of_acquisition_paise: int
    gain_paise: int  # sale_value_paise - cost_of_acquisition_paise, always exact
    holding_period_days: int
    gain_type: GainType
    tax_asset_class: TaxAssetClass | None  # None only if scheme_metadata is missing entirely


@dataclass(frozen=True)
class CapitalGainsReport:
    financial_year: str
    lines: list[CapitalGainsLine]
    total_stcg_paise: int
    total_ltcg_paise: int
    total_unclassified_paise: int
    schemes_missing_metadata: int


def financial_year_bounds(fy_label: str) -> tuple[date, date]:
    """"2024-25" -> (2024-04-01, 2025-03-31)."""
    start_year = int(fy_label.split("-")[0])
    return date(start_year, 4, 1), date(start_year + 1, 3, 31)


def get_capital_gains_report(session: Session, fy_label: str) -> CapitalGainsReport:
    fy_start, fy_end = financial_year_bounds(fy_label)

    gains = session.scalars(
        select(RealizedGain).where(
            RealizedGain.sale_date >= fy_start, RealizedGain.sale_date <= fy_end
        )
    ).all()

    if not gains:
        return CapitalGainsReport(fy_label, [], 0, 0, 0, 0)

    sale_txn_ids = {g.sale_txn_id for g in gains}
    sale_txns = {
        t.id: t
        for t in session.scalars(
            select(RawTransaction).where(RawTransaction.id.in_(sale_txn_ids))
        )
    }

    amfi_codes = {t.amfi_code for t in sale_txns.values() if t.amfi_code}
    metadata_by_code = {
        m.amfi_code: m
        for m in session.scalars(
            select(SchemeMetadata).where(SchemeMetadata.amfi_code.in_(amfi_codes))
        )
    }

    lines: list[CapitalGainsLine] = []
    total_stcg = 0
    total_ltcg = 0
    total_unclassified = 0
    schemes_missing_metadata: set[str] = set()

    for gain in gains:
        sale_txn = sale_txns.get(gain.sale_txn_id)
        metadata = (
            metadata_by_code.get(sale_txn.amfi_code)
            if sale_txn and sale_txn.amfi_code
            else None
        )

        if metadata is None:
            schemes_missing_metadata.add(gain.scheme_key)
            classification = GainClassification(
                GainType.UNCLASSIFIED, reason="scheme_metadata_unavailable"
            )
        else:
            classification = classify_gain(
                metadata.tax_asset_class, gain.open_date, gain.holding_period_days
            )

        sale_value_paise = to_paise_strict(gain.units_consumed * gain.sale_price_per_unit)
        cost_paise = to_paise_strict(gain.units_consumed * gain.cost_per_unit)

        lines.append(
            CapitalGainsLine(
                folio=gain.folio,
                scheme_key=gain.scheme_key,
                scheme_name=sale_txn.scheme_name if sale_txn else gain.scheme_key,
                isin=sale_txn.isin if sale_txn else None,
                amfi_code=sale_txn.amfi_code if sale_txn else None,
                purchase_date=gain.open_date,
                sale_date=gain.sale_date,
                units=gain.units_consumed,
                sale_value_paise=sale_value_paise,
                cost_of_acquisition_paise=cost_paise,
                # Derived, not gain.realized_gain_paise verbatim: keeps
                # sale_value - cost == gain exactly in the report, rather
                # than risking a stray 1-paise mismatch from two
                # independently-rounded figures.
                gain_paise=sale_value_paise - cost_paise,
                holding_period_days=gain.holding_period_days,
                gain_type=classification.gain_type,
                tax_asset_class=metadata.tax_asset_class if metadata else None,
            )
        )

        line_gain = sale_value_paise - cost_paise
        if classification.gain_type == GainType.STCG:
            total_stcg += line_gain
        elif classification.gain_type == GainType.LTCG:
            total_ltcg += line_gain
        else:
            total_unclassified += line_gain

    lines.sort(key=lambda line: (line.folio, line.scheme_name, line.sale_date))

    return CapitalGainsReport(
        financial_year=fy_label,
        lines=lines,
        total_stcg_paise=total_stcg,
        total_ltcg_paise=total_ltcg,
        total_unclassified_paise=total_unclassified,
        schemes_missing_metadata=len(schemes_missing_metadata),
    )
