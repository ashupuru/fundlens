"""STCG/LTCG classification per realized gain (PRD FR-10.2, compute-engine.md §3).

Minimal scope for P0.5, per the dev plan: equity/debt LTCG/STCG
classification only. Full grandfathering (the Jan-31-2018 FMV cost basis for
pre-2018 equity purchases) and ₹1.25L/FY exemption tracking are P1 — this
module classifies STCG vs LTCG and reports raw gains, before exemption.

Rules (current Indian tax law: Finance Act 2023's "specified mutual fund"
debt-taxation change, effective 1 Apr 2023):
- EQUITY-oriented: holding period > 12 months => LTCG, else STCG.
- DEBT-oriented, purchased on/after 1 Apr 2023: no LTCG concept at all —
  always taxed at slab rate (reported here as STCG, since that's the
  closest existing bucket; the report should not be read as implying a
  12-month threshold applies).
- DEBT-oriented, purchased before 1 Apr 2023 (old regime, grandfathered):
  holding period > 36 months => LTCG, else STCG.
- NEEDS_REVIEW asset class: the gain is reported but explicitly
  UNCLASSIFIED, never silently defaulted into either bucket — an incorrect
  equity/debt call changes the tax owed.

The 12/36-*month* legal thresholds are approximated here as 365/1095 *days*
— calendar-month-exact comparison would need month/day arithmetic, and the
day-count approximation can differ by at most ~1 day in rare leap-year edge
cases. Documented, not hidden.
"""

import enum
from dataclasses import dataclass
from datetime import date

from fundlens.tax.asset_class import TaxAssetClass

EQUITY_LTCG_THRESHOLD_DAYS = 365
DEBT_LTCG_THRESHOLD_DAYS = 3 * 365
DEBT_SPECIFIED_FUND_CUTOFF = date(2023, 4, 1)


class GainType(enum.StrEnum):
    STCG = "stcg"
    LTCG = "ltcg"
    UNCLASSIFIED = "unclassified"


@dataclass(frozen=True)
class GainClassification:
    gain_type: GainType
    reason: str | None = None  # set only for UNCLASSIFIED


def classify_gain(
    asset_class: TaxAssetClass,
    purchase_date: date,
    holding_period_days: int,
) -> GainClassification:
    if asset_class == TaxAssetClass.NEEDS_REVIEW:
        return GainClassification(
            GainType.UNCLASSIFIED, reason="scheme_category_needs_review"
        )

    if asset_class == TaxAssetClass.EQUITY:
        gain_type = (
            GainType.LTCG if holding_period_days > EQUITY_LTCG_THRESHOLD_DAYS else GainType.STCG
        )
        return GainClassification(gain_type)

    # DEBT
    if purchase_date >= DEBT_SPECIFIED_FUND_CUTOFF:
        return GainClassification(GainType.STCG)

    gain_type = (
        GainType.LTCG if holding_period_days > DEBT_LTCG_THRESHOLD_DAYS else GainType.STCG
    )
    return GainClassification(gain_type)
