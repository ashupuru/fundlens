"""SELL friction statement (compute-engine.md §3.5, FR-6.2): for a
currently-held position, the real rupee tax cost if sold today. Required
on every non-HOLD verdict (compute/verdict.py) — schema-enforced, not just
UI-enforced.

Per-lot, not per-position: a position built from multiple purchases can
have both STCG- and LTCG-eligible lots simultaneously, so this replays
compute/capital_gains.py's classification and grandfathering logic
(tax/classification.py, tax/grandfathering.py) per open lot rather than
treating the position as one undifferentiated block — the same machinery
the realized-gains report already uses, applied to a hypothetical sale
today instead of a past sale.

**Two real data gaps, handled by flagging rather than guessing**:
- **Debt tax rate**: debt gains are taxed at the investor's marginal
  income-tax slab, which nothing in this app captures (unlike equity's
  fixed, publicly-known STCG/LTCG rates). Requires
  `debt_marginal_tax_rate_pct` to be set via POST /assumptions first: if
  unset, `computable=False, reason="debt_marginal_tax_rate_not_configured"`
  — never defaulted to an assumed slab.
- **Exit load**: no free data source for exit-load schedules exists
  anywhere in this app (same category of gap as expense ratio, P1.3's own
  scope-narrowing). `exit_load_modeled` is always `False` and the
  breakeven figure is tax-only, never silently presented as
  tax-plus-exit-load.

Old-regime debt LTCG (pre-2023 purchases, >36mo) is taxed the same as
debt STCG here (`debt_marginal_tax_rate_pct`) rather than modeling
indexation, which would need historical Cost Inflation Index data this
app doesn't have — a known simplification, not a hidden one.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from fundlens.assumptions.service import (
    get_current_rubric_tax_rates,
    get_current_tax_assumptions,
)
from fundlens.compute.capital_gains import financial_year_bounds, get_capital_gains_report
from fundlens.compute.holdings import get_current_holdings
from fundlens.compute.metrics import latest_nav
from fundlens.db.models import Lot, SchemeMetadata
from fundlens.money import to_paise_strict
from fundlens.tax.asset_class import TaxAssetClass
from fundlens.tax.classification import GainType, classify_gain
from fundlens.tax.grandfathering import apply_equity_grandfathering


@dataclass(frozen=True)
class TaxFrictionStatement:
    folio: str
    scheme_key: str
    computable: bool
    reason: str | None  # set only when computable is False
    stcg_gain_paise: int
    ltcg_gain_paise: int  # after grandfathering, where applicable
    ltcg_exemption_available_paise: int  # this PAN's remaining headroom this FY
    ltcg_exemption_consumed_paise: int
    taxable_ltcg_paise: int
    tax_owed_paise: int  # 0 when not computable
    exit_load_modeled: bool  # always False — no data source, see module docstring
    breakeven_note: str


def _current_fy_label(today: date) -> str:
    start_year = today.year if today.month >= 4 else today.year - 1
    return f"{start_year}-{str(start_year + 1)[2:]}"


def _not_computable(folio: str, scheme_key: str, reason: str) -> TaxFrictionStatement:
    return TaxFrictionStatement(
        folio=folio,
        scheme_key=scheme_key,
        computable=False,
        reason=reason,
        stcg_gain_paise=0,
        ltcg_gain_paise=0,
        ltcg_exemption_available_paise=0,
        ltcg_exemption_consumed_paise=0,
        taxable_ltcg_paise=0,
        tax_owed_paise=0,
        exit_load_modeled=False,
        breakeven_note="Not computable — see reason.",
    )


def compute_tax_friction(session: Session, folio: str, scheme_key: str) -> TaxFrictionStatement:
    holding = next(
        (
            h
            for h in get_current_holdings(session)
            if h.folio == folio and h.scheme_key == scheme_key
        ),
        None,
    )
    if holding is None or holding.units_remaining == 0:
        return _not_computable(folio, scheme_key, "no_open_position")

    metadata = session.get(SchemeMetadata, holding.amfi_code) if holding.amfi_code else None
    if metadata is None:
        return _not_computable(folio, scheme_key, "scheme_metadata_unavailable")
    if metadata.tax_asset_class == TaxAssetClass.NEEDS_REVIEW:
        return _not_computable(folio, scheme_key, "scheme_category_needs_review")

    nav_info = latest_nav(session, holding.amfi_code) if holding.amfi_code else None
    if nav_info is None:
        return _not_computable(folio, scheme_key, "nav_unavailable")
    current_nav, _as_of = nav_info

    rates = get_current_rubric_tax_rates(session)
    if metadata.tax_asset_class == TaxAssetClass.DEBT and rates.debt_marginal_tax_rate_pct is None:
        return _not_computable(folio, scheme_key, "debt_marginal_tax_rate_not_configured")

    tax_assumptions = get_current_tax_assumptions(session)
    today = date.today()

    lots = session.scalars(
        select(Lot).where(Lot.folio == folio, Lot.scheme_key == scheme_key, Lot.units_remaining > 0)
    ).all()

    stcg_gain_paise = 0
    ltcg_gain_paise = 0
    for lot in lots:
        holding_period_days = (today - lot.open_date).days
        classification = classify_gain(
            metadata.tax_asset_class, lot.open_date, holding_period_days, tax_assumptions
        )

        cost_per_unit = lot.cost_per_unit
        is_equity_ltcg = (
            metadata.tax_asset_class == TaxAssetClass.EQUITY
            and classification.gain_type == GainType.LTCG
        )
        if is_equity_ltcg:
            gf = apply_equity_grandfathering(
                session,
                holding.amfi_code,
                lot.open_date,
                lot.cost_per_unit,
                current_nav,
                tax_assumptions.equity_grandfather_cutoff,
            )
            cost_per_unit = gf.cost_per_unit

        sale_value_paise = to_paise_strict(lot.units_remaining * current_nav)
        cost_paise = to_paise_strict(lot.units_remaining * cost_per_unit)
        gain_paise = sale_value_paise - cost_paise

        if classification.gain_type == GainType.STCG:
            stcg_gain_paise += gain_paise
        else:  # LTCG — NEEDS_REVIEW was already ruled out above
            ltcg_gain_paise += gain_paise

    exemption_available_paise = tax_assumptions.equity_ltcg_exemption_paise_per_fy
    exemption_consumed_paise = 0
    if metadata.tax_asset_class == TaxAssetClass.EQUITY and holding.pan:
        current_fy = _current_fy_label(today)
        fy_start, fy_end = financial_year_bounds(current_fy)
        if fy_start <= today <= fy_end:
            report = get_capital_gains_report(session, current_fy)
            already_consumed = sum(
                line.ltcg_exemption_consumed_paise
                for line in report.lines
                if line.pan == holding.pan
            )
            exemption_available_paise = max(
                tax_assumptions.equity_ltcg_exemption_paise_per_fy - already_consumed, 0
            )
        exemption_consumed_paise = min(max(ltcg_gain_paise, 0), exemption_available_paise)

    taxable_ltcg_paise = max(ltcg_gain_paise, 0) - exemption_consumed_paise

    if metadata.tax_asset_class == TaxAssetClass.EQUITY:
        tax_owed_paise = int(
            max(stcg_gain_paise, 0) * rates.equity_stcg_rate_pct / Decimal(100)
        ) + int(taxable_ltcg_paise * rates.equity_ltcg_rate_pct / Decimal(100))
    else:
        assert rates.debt_marginal_tax_rate_pct is not None  # checked above
        total_debt_gain_paise = max(stcg_gain_paise + ltcg_gain_paise, 0)
        debt_rate = rates.debt_marginal_tax_rate_pct
        tax_owed_paise = int(total_debt_gain_paise * debt_rate / Decimal(100))

    return TaxFrictionStatement(
        folio=folio,
        scheme_key=scheme_key,
        computable=True,
        reason=None,
        stcg_gain_paise=stcg_gain_paise,
        ltcg_gain_paise=ltcg_gain_paise,
        ltcg_exemption_available_paise=exemption_available_paise,
        ltcg_exemption_consumed_paise=exemption_consumed_paise,
        taxable_ltcg_paise=taxable_ltcg_paise,
        tax_owed_paise=tax_owed_paise,
        exit_load_modeled=False,
        breakeven_note=(
            "Tax cost only — exit load isn't modeled (no data source available). "
            "The real cost of switching may be higher than shown."
        ),
    )
