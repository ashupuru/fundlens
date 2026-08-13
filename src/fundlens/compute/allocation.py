"""Allocation & Drift (PRD FR-7.1, compute-engine.md §7): actual portfolio
allocation vs the active risk profile's target equity:debt bands.

Actual allocation is **current market value**, not cost basis — asset
allocation is about what fraction of the portfolio's value sits where
today, not what fraction of money originally invested went where (cost
basis would misrepresent allocation after any fund's price has moved).
Computed from currently-open holdings (compute/holdings.py) priced at each
scheme's latest available NAV (compute/metrics.py::latest_nav); a holding
with no NAV data is excluded from the percentage totals and counted
separately (`value_unavailable_paise`) rather than silently valued at zero
or at cost, either of which would misstate the drift numbers.

Grouped by `SchemeMetadata.tax_asset_class`: EQUITY / DEBT, with everything
else (`NEEDS_REVIEW`-classified schemes, or schemes entirely missing
scheme_metadata) bucketed as "other" and reported separately rather than
silently folded into equity or debt — the PRD's persona table (§3) only
defines an equity:debt target ratio, so "other" has no target to drift
against.

Equity holdings are further broken down by cap size
(tax/asset_class.py::equity_cap_size) as informational detail only — no
persona in the PRD defines large/mid/small sub-targets, so this breakdown
is reported, never drift-scored against an invented target.
"""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from fundlens.compute.holdings import get_current_holdings
from fundlens.compute.metrics import latest_nav
from fundlens.db.models import Persona, RiskProfile, SchemeMetadata
from fundlens.money import to_paise_strict
from fundlens.tax.asset_class import EquityCapSize, TaxAssetClass, equity_cap_size


@dataclass(frozen=True)
class EquityCapBreakdown:
    large_paise: int
    mid_paise: int
    small_paise: int
    large_and_mid_paise: int
    other_paise: int


@dataclass(frozen=True)
class AllocationDrift:
    persona: Persona
    target_equity_pct: float
    target_debt_pct: float
    actual_equity_pct: float | None
    actual_debt_pct: float | None
    actual_other_pct: float | None
    equity_drift_pct: float | None
    debt_drift_pct: float | None
    priced_portfolio_value_paise: int
    equity_value_paise: int
    debt_value_paise: int
    other_value_paise: int
    value_unavailable_paise: int
    equity_cap_breakdown: EquityCapBreakdown
    schemes_missing_metadata: int


_CAP_SIZE_FIELD = {
    EquityCapSize.LARGE: "large_paise",
    EquityCapSize.MID: "mid_paise",
    EquityCapSize.SMALL: "small_paise",
    EquityCapSize.LARGE_AND_MID: "large_and_mid_paise",
    EquityCapSize.OTHER: "other_paise",
}


def compute_allocation_drift(session: Session, profile: RiskProfile) -> AllocationDrift:
    holdings = get_current_holdings(session)
    amfi_codes = {h.amfi_code for h in holdings if h.amfi_code}
    metadata_by_code = {
        m.amfi_code: m
        for m in session.scalars(
            select(SchemeMetadata).where(SchemeMetadata.amfi_code.in_(amfi_codes))
        )
    }

    equity_paise = 0
    debt_paise = 0
    other_paise = 0
    unavailable_paise = 0
    missing_metadata = 0
    cap_paise = {size: 0 for size in EquityCapSize}

    for h in holdings:
        nav_info = latest_nav(session, h.amfi_code) if h.amfi_code else None
        if nav_info is None:
            # cost_basis is a floor, not a market value -- don't silently
            # substitute it. Flagged and excluded from the percentage totals.
            unavailable_paise += h.cost_basis_paise
            continue
        nav, _as_of = nav_info
        value_paise = to_paise_strict(h.units_remaining * nav)

        metadata = metadata_by_code.get(h.amfi_code) if h.amfi_code else None
        if metadata is None:
            missing_metadata += 1
            other_paise += value_paise
        elif metadata.tax_asset_class == TaxAssetClass.EQUITY:
            equity_paise += value_paise
            cap_paise[equity_cap_size(metadata.category_raw)] += value_paise
        elif metadata.tax_asset_class == TaxAssetClass.DEBT:
            debt_paise += value_paise
        else:
            other_paise += value_paise

    priced_total = equity_paise + debt_paise + other_paise

    actual_equity_pct: float | None = None
    actual_debt_pct: float | None = None
    actual_other_pct: float | None = None
    equity_drift_pct: float | None = None
    debt_drift_pct: float | None = None
    if priced_total > 0:
        actual_equity_pct = equity_paise / priced_total * 100
        actual_debt_pct = debt_paise / priced_total * 100
        actual_other_pct = other_paise / priced_total * 100
        equity_drift_pct = actual_equity_pct - float(profile.target_equity_pct)
        debt_drift_pct = actual_debt_pct - float(profile.target_debt_pct)

    return AllocationDrift(
        persona=profile.persona,
        target_equity_pct=float(profile.target_equity_pct),
        target_debt_pct=float(profile.target_debt_pct),
        actual_equity_pct=actual_equity_pct,
        actual_debt_pct=actual_debt_pct,
        actual_other_pct=actual_other_pct,
        equity_drift_pct=equity_drift_pct,
        debt_drift_pct=debt_drift_pct,
        priced_portfolio_value_paise=priced_total,
        equity_value_paise=equity_paise,
        debt_value_paise=debt_paise,
        other_value_paise=other_paise,
        value_unavailable_paise=unavailable_paise,
        equity_cap_breakdown=EquityCapBreakdown(
            large_paise=cap_paise[EquityCapSize.LARGE],
            mid_paise=cap_paise[EquityCapSize.MID],
            small_paise=cap_paise[EquityCapSize.SMALL],
            large_and_mid_paise=cap_paise[EquityCapSize.LARGE_AND_MID],
            other_paise=cap_paise[EquityCapSize.OTHER],
        ),
        schemes_missing_metadata=missing_metadata,
    )


def compute_category_fit_score(
    tax_asset_class: TaxAssetClass, drift: AllocationDrift
) -> tuple[float | None, str | None]:
    """Rubric component (compute-engine.md §5): 0-100, 50 = neutral. Below
    50 means holding more of this category would worsen an existing
    overweight; above 50 means it would help correct an underweight.

    The "1 percentage point of drift = 0.5 points of score, capped at
    ±50-from-neutral" scaling is a judgment call — the PRD only specifies
    the signal's *direction* ("actual-vs-target allocation drift
    contribution"), not a formula. Documented as a choice, not asserted as
    canonical.
    """
    if drift.equity_drift_pct is None or drift.debt_drift_pct is None:
        return None, "portfolio_not_priced"
    if tax_asset_class == TaxAssetClass.EQUITY:
        relevant_drift = drift.equity_drift_pct
    elif tax_asset_class == TaxAssetClass.DEBT:
        relevant_drift = drift.debt_drift_pct
    else:
        # NEEDS_REVIEW: "other" has no target (see module docstring), so
        # there's nothing to score fit against.
        return None, "no_target_for_category"

    score = 50 - relevant_drift / 2
    return max(0.0, min(100.0, score)), None
