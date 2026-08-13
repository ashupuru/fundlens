"""Capital gains report (PRD FR-10.2): scheme-wise STCG/LTCG for a
financial year, sourced directly from the FIFO realized-gain ledger
(compute/lots.py) — never recomputed independently, so the report can never
drift from what the lot allocator actually determined.

FY-aligned (Apr-Mar, PRD §10.1). P1.2 adds the two pieces P0.5 deliberately
deferred:

- **Equity LTCG grandfathering** (Section 112A 4th/5th proviso): for
  equity-oriented units purchased before the assumption registry's
  `equity_grandfather_cutoff` (2018-01-31 by default), the cost of
  acquisition used for the *gain amount* is the higher of (a) actual cost
  and (b) the lower of (i) FMV as of the cutoff date and (ii) sale price —
  never the classification (STCG vs LTCG) itself, only the cost basis LTCG
  gain is computed from. FMV is read from the same `nav_history` table
  P0.2 already backfills (a scheme's *full* history, not just from its
  first purchase), so no new data source is needed. If a scheme predates
  the cutoff but has no NAV on/before it (backfill gap), grandfathering is
  **not silently skipped in the direction that understates tax** — the
  line is flagged `grandfathering_note="grandfathering_data_unavailable"`
  and actual cost is used, which is the conservative (higher-tax) fallback
  when the true FMV can't be determined.
- **LTCG exemption tracking** (₹1.25L/FY, PAN-scoped per PRD §12.2 — tax
  math never crosses PAN boundaries): equity LTCG lines are grouped by the
  sale transaction's PAN, ordered by `sale_date`, and consume the
  exemption headroom first-realized-first-consumed. A line with a gain
  but no PAN on its transaction can't be scoped to any PAN's headroom —
  flagged `exemption_note="no_pan_on_transaction"` and treated as fully
  taxable (the conservative direction) rather than guessed into some PAN's
  bucket. Losses (`gain_paise <= 0`) never consume headroom.

Both mechanisms read their thresholds from the assumption registry
(`assumptions/service.py::TaxAssumptions`), not code constants, so a report
generated under one assumption version stays reproducible after the user
edits an assumption later.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from fundlens.assumptions.service import get_current_tax_assumptions
from fundlens.db.models import NavHistory, RawTransaction, RealizedGain, SchemeMetadata
from fundlens.money import to_paise_strict
from fundlens.tax.asset_class import TaxAssetClass
from fundlens.tax.classification import GainClassification, GainType, classify_gain


@dataclass(frozen=True)
class CapitalGainsLine:
    folio: str
    pan: str | None
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
    grandfathering_applied: bool
    grandfathering_note: str | None
    ltcg_exemption_consumed_paise: int
    taxable_gain_paise: int
    exemption_note: str | None


@dataclass(frozen=True)
class CapitalGainsReport:
    financial_year: str
    lines: list[CapitalGainsLine]
    total_stcg_paise: int
    total_ltcg_paise: int
    total_unclassified_paise: int
    total_taxable_gain_paise: int
    total_ltcg_exemption_used_paise: int
    schemes_missing_metadata: int


def financial_year_bounds(fy_label: str) -> tuple[date, date]:
    """"2024-25" -> (2024-04-01, 2025-03-31)."""
    start_year = int(fy_label.split("-")[0])
    return date(start_year, 4, 1), date(start_year + 1, 3, 31)


def _fmv_per_unit_on_or_before(
    session: Session, amfi_code: str, on_date: date
) -> Decimal | None:
    return session.execute(
        select(NavHistory.nav)
        .where(NavHistory.amfi_code == amfi_code, NavHistory.nav_date <= on_date)
        .order_by(NavHistory.nav_date.desc())
        .limit(1)
    ).scalar_one_or_none()


@dataclass(frozen=True)
class _Grandfathering:
    cost_per_unit: Decimal
    applied: bool
    note: str | None


def _apply_equity_grandfathering(
    session: Session,
    amfi_code: str | None,
    purchase_date: date,
    original_cost_per_unit: Decimal,
    sale_price_per_unit: Decimal,
    cutoff: date,
) -> _Grandfathering:
    if purchase_date >= cutoff:
        return _Grandfathering(original_cost_per_unit, applied=False, note=None)
    if amfi_code is None:
        return _Grandfathering(
            original_cost_per_unit, applied=False, note="grandfathering_data_unavailable"
        )

    fmv = _fmv_per_unit_on_or_before(session, amfi_code, cutoff)
    if fmv is None:
        return _Grandfathering(
            original_cost_per_unit, applied=False, note="grandfathering_data_unavailable"
        )

    floor = min(fmv, sale_price_per_unit)
    adjusted = max(original_cost_per_unit, floor)
    return _Grandfathering(adjusted, applied=adjusted != original_cost_per_unit, note=None)


def _apply_ltcg_exemption(
    raw_lines: list[dict[str, Any]], exemption_paise_per_fy: int
) -> None:
    """Mutates each raw_line dict in place, setting
    ltcg_exemption_consumed_paise/taxable_gain_paise/exemption_note."""
    by_pan: dict[str, list[dict[str, Any]]] = {}
    for rl in raw_lines:
        is_equity_ltcg = (
            rl["gain_type"] == GainType.LTCG and rl["tax_asset_class"] == TaxAssetClass.EQUITY
        )
        if not is_equity_ltcg:
            rl["ltcg_exemption_consumed_paise"] = 0
            rl["taxable_gain_paise"] = rl["gain_paise"]
            rl["exemption_note"] = None
            continue
        if rl["gain_paise"] <= 0:
            rl["ltcg_exemption_consumed_paise"] = 0
            rl["taxable_gain_paise"] = rl["gain_paise"]
            rl["exemption_note"] = None
            continue
        if not rl["pan"]:
            rl["ltcg_exemption_consumed_paise"] = 0
            rl["taxable_gain_paise"] = rl["gain_paise"]
            rl["exemption_note"] = "no_pan_on_transaction"
            continue
        by_pan.setdefault(rl["pan"], []).append(rl)

    for pan_lines in by_pan.values():
        pan_lines.sort(key=lambda rl: rl["sale_date"])
        headroom = exemption_paise_per_fy
        for rl in pan_lines:
            consumed = min(rl["gain_paise"], headroom)
            rl["ltcg_exemption_consumed_paise"] = consumed
            rl["taxable_gain_paise"] = rl["gain_paise"] - consumed
            rl["exemption_note"] = None
            headroom -= consumed


def get_capital_gains_report(session: Session, fy_label: str) -> CapitalGainsReport:
    fy_start, fy_end = financial_year_bounds(fy_label)
    assumptions = get_current_tax_assumptions(session)

    gains = session.scalars(
        select(RealizedGain).where(
            RealizedGain.sale_date >= fy_start, RealizedGain.sale_date <= fy_end
        )
    ).all()

    if not gains:
        return CapitalGainsReport(fy_label, [], 0, 0, 0, 0, 0, 0)

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

    schemes_missing_metadata: set[str] = set()
    raw_lines: list[dict[str, Any]] = []

    for gain in gains:
        sale_txn = sale_txns.get(gain.sale_txn_id)
        metadata = (
            metadata_by_code.get(sale_txn.amfi_code)
            if sale_txn and sale_txn.amfi_code
            else None
        )

        if metadata is None:
            schemes_missing_metadata.add(gain.scheme_key)
            classification: GainClassification = GainClassification(
                GainType.UNCLASSIFIED, reason="scheme_metadata_unavailable"
            )
        else:
            classification = classify_gain(
                metadata.tax_asset_class, gain.open_date, gain.holding_period_days, assumptions
            )

        cost_per_unit = gain.cost_per_unit
        grandfathering = _Grandfathering(cost_per_unit, applied=False, note=None)
        if (
            metadata is not None
            and metadata.tax_asset_class == TaxAssetClass.EQUITY
            and classification.gain_type == GainType.LTCG
        ):
            grandfathering = _apply_equity_grandfathering(
                session,
                sale_txn.amfi_code if sale_txn else None,
                gain.open_date,
                gain.cost_per_unit,
                gain.sale_price_per_unit,
                assumptions.equity_grandfather_cutoff,
            )
            cost_per_unit = grandfathering.cost_per_unit

        sale_value_paise = to_paise_strict(gain.units_consumed * gain.sale_price_per_unit)
        cost_paise = to_paise_strict(gain.units_consumed * cost_per_unit)
        gain_paise = sale_value_paise - cost_paise

        raw_lines.append(
            {
                "folio": gain.folio,
                "pan": sale_txn.pan if sale_txn else None,
                "scheme_key": gain.scheme_key,
                "scheme_name": sale_txn.scheme_name if sale_txn else gain.scheme_key,
                "isin": sale_txn.isin if sale_txn else None,
                "amfi_code": sale_txn.amfi_code if sale_txn else None,
                "purchase_date": gain.open_date,
                "sale_date": gain.sale_date,
                "units": gain.units_consumed,
                "sale_value_paise": sale_value_paise,
                "cost_of_acquisition_paise": cost_paise,
                "gain_paise": gain_paise,
                "holding_period_days": gain.holding_period_days,
                "gain_type": classification.gain_type,
                "tax_asset_class": metadata.tax_asset_class if metadata else None,
                "grandfathering_applied": grandfathering.applied,
                "grandfathering_note": grandfathering.note,
            }
        )

    _apply_ltcg_exemption(raw_lines, assumptions.equity_ltcg_exemption_paise_per_fy)

    lines = [
        CapitalGainsLine(
            folio=rl["folio"],
            pan=rl["pan"],
            scheme_key=rl["scheme_key"],
            scheme_name=rl["scheme_name"],
            isin=rl["isin"],
            amfi_code=rl["amfi_code"],
            purchase_date=rl["purchase_date"],
            sale_date=rl["sale_date"],
            units=rl["units"],
            sale_value_paise=rl["sale_value_paise"],
            cost_of_acquisition_paise=rl["cost_of_acquisition_paise"],
            gain_paise=rl["gain_paise"],
            holding_period_days=rl["holding_period_days"],
            gain_type=rl["gain_type"],
            tax_asset_class=rl["tax_asset_class"],
            grandfathering_applied=rl["grandfathering_applied"],
            grandfathering_note=rl["grandfathering_note"],
            ltcg_exemption_consumed_paise=rl["ltcg_exemption_consumed_paise"],
            taxable_gain_paise=rl["taxable_gain_paise"],
            exemption_note=rl["exemption_note"],
        )
        for rl in raw_lines
    ]
    lines.sort(key=lambda line: (line.folio, line.scheme_name, line.sale_date))

    total_stcg = sum(rl["gain_paise"] for rl in raw_lines if rl["gain_type"] == GainType.STCG)
    total_ltcg = sum(rl["gain_paise"] for rl in raw_lines if rl["gain_type"] == GainType.LTCG)
    total_unclassified = sum(
        rl["gain_paise"] for rl in raw_lines if rl["gain_type"] == GainType.UNCLASSIFIED
    )
    total_taxable = sum(rl["taxable_gain_paise"] for rl in raw_lines)
    total_exemption_used = sum(rl["ltcg_exemption_consumed_paise"] for rl in raw_lines)

    return CapitalGainsReport(
        financial_year=fy_label,
        lines=lines,
        total_stcg_paise=total_stcg,
        total_ltcg_paise=total_ltcg,
        total_unclassified_paise=total_unclassified,
        total_taxable_gain_paise=total_taxable,
        total_ltcg_exemption_used_paise=total_exemption_used,
        schemes_missing_metadata=len(schemes_missing_metadata),
    )
