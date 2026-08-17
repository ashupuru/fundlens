"""Goal Planning (PRD FR-8.1/8.2, phase P2.4, compute-engine.md §10):
projected corpus, required monthly SIP, and a ±2% return-sensitivity table
for a savings goal built from tagged holdings.

Compute-on-demand, never persisted — same "derive fresh, don't cache
prematurely" philosophy as compute_allocation_drift/verdict/benchmark
comparison. Money stays in Decimal throughout the growth math (not float)
to match this codebase's money.py convention; only converted to int paise
at the boundary.
"""

from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy.orm import Session

from fundlens.assumptions.service import GoalAssumptions
from fundlens.compute.holdings import get_current_holdings
from fundlens.compute.metrics import latest_nav
from fundlens.db.models import Goal, RiskProfile
from fundlens.money import to_paise_strict

_SENSITIVITY_DELTA_PCT = Decimal("2")


def _round_paise(value: Decimal) -> int:
    return int(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _fv_lump_sum(present_value_paise: int, annual_rate_pct: Decimal, years: Decimal) -> Decimal:
    growth = (Decimal(1) + annual_rate_pct / 100) ** years
    return Decimal(present_value_paise) * growth


def _required_monthly_sip_paise(
    gap_paise: Decimal, annual_rate_pct: Decimal, years: Decimal
) -> int:
    """Standard future-value-of-an-ordinary-annuity formula, solved for the
    payment: FV = P * (((1+i)^n - 1) / i), monthly compounding.
    """
    if gap_paise <= 0:
        return 0
    monthly_rate = annual_rate_pct / 100 / 12
    n_months = years * 12
    if monthly_rate == 0:
        return _round_paise(gap_paise / n_months)
    growth_factor = ((Decimal(1) + monthly_rate) ** n_months - Decimal(1)) / monthly_rate
    return _round_paise(gap_paise / growth_factor)


@dataclass(frozen=True)
class SensitivityPoint:
    return_pct: Decimal
    required_monthly_sip_paise: int


@dataclass(frozen=True)
class GoalProjection:
    goal_id: str
    years_to_target: float
    current_corpus_paise: int
    current_corpus_unavailable_paise: int
    inflated_target_paise: int
    blended_expected_return_pct: Decimal
    projected_corpus_paise: int
    surplus_or_shortfall_paise: int
    required_monthly_sip_paise: int
    sensitivity: list[SensitivityPoint]


def compute_goal_projection(
    session: Session,
    goal: Goal,
    tagged_holdings: set[tuple[str, str]],
    risk_profile: RiskProfile,
    assumptions: GoalAssumptions,
) -> GoalProjection:
    current_corpus_paise = 0
    current_corpus_unavailable_paise = 0
    for h in get_current_holdings(session):
        if (h.folio, h.scheme_key) not in tagged_holdings:
            continue
        nav_info = latest_nav(session, h.amfi_code) if h.amfi_code else None
        if nav_info is None:
            # cost_basis is a floor, not a market value -- flagged and
            # excluded from the priced corpus, same pattern as
            # compute/allocation.py's unavailable_paise.
            current_corpus_unavailable_paise += h.cost_basis_paise
            continue
        nav, _as_of = nav_info
        current_corpus_paise += to_paise_strict(h.units_remaining * nav)

    years = Decimal(str((goal.target_date - date.today()).days / 365.0))

    blended_return_pct = (
        risk_profile.target_equity_pct / 100 * assumptions.expected_equity_return_pct
        + risk_profile.target_debt_pct / 100 * assumptions.expected_debt_return_pct
    )

    inflated_target_paise = _round_paise(
        _fv_lump_sum(goal.target_amount_paise, assumptions.inflation_pct, years)
    )

    def _at_rate(rate_pct: Decimal) -> tuple[int, int]:
        projected = _fv_lump_sum(current_corpus_paise, rate_pct, years)
        gap = Decimal(inflated_target_paise) - projected
        required_sip = _required_monthly_sip_paise(gap, rate_pct, years)
        return _round_paise(projected), required_sip

    projected_corpus_paise, required_monthly_sip_paise = _at_rate(blended_return_pct)

    sensitivity = [
        SensitivityPoint(
            return_pct=rate,
            required_monthly_sip_paise=_at_rate(rate)[1],
        )
        for rate in (
            blended_return_pct - _SENSITIVITY_DELTA_PCT,
            blended_return_pct,
            blended_return_pct + _SENSITIVITY_DELTA_PCT,
        )
    ]

    return GoalProjection(
        goal_id=goal.id,
        years_to_target=years.__float__(),
        current_corpus_paise=current_corpus_paise,
        current_corpus_unavailable_paise=current_corpus_unavailable_paise,
        inflated_target_paise=inflated_target_paise,
        blended_expected_return_pct=blended_return_pct,
        projected_corpus_paise=projected_corpus_paise,
        surplus_or_shortfall_paise=projected_corpus_paise - inflated_target_paise,
        required_monthly_sip_paise=required_monthly_sip_paise,
        sensitivity=sensitivity,
    )
