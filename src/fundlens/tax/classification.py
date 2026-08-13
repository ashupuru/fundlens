"""STCG/LTCG classification per realized gain (PRD FR-10.2, compute-engine.md §3).

Thresholds are versioned, editable config (PRD §5.7 assumption registry,
`fundlens/assumptions/service.py::TaxAssumptions`), not code constants — a
report generated under one assumption version stays reproducible even
after the user edits an assumption later.

Rules (current Indian tax law: Finance Act 2023's "specified mutual fund"
debt-taxation change, effective 1 Apr 2023):
- EQUITY-oriented: holding period > `equity_ltcg_threshold_days` => LTCG,
  else STCG. (Equity LTCG grandfathering — the Jan-31-2018 FMV cost-basis
  rule — adjusts the *cost basis* used to compute the gain amount, not
  this STCG/LTCG classification; see compute/capital_gains.py.)
- DEBT-oriented, purchased on/after `debt_specified_fund_cutoff`: no LTCG
  concept at all — always taxed at slab rate (reported here as STCG, since
  that's the closest existing bucket; the report should not be read as
  implying a holding-period threshold applies).
- DEBT-oriented, purchased before the cutoff (old regime, grandfathered):
  holding period > `debt_ltcg_threshold_days` => LTCG, else STCG.
- NEEDS_REVIEW asset class: the gain is reported but explicitly
  UNCLASSIFIED, never silently defaulted into either bucket — an incorrect
  equity/debt call changes the tax owed.

The 12/36-*month* legal thresholds are approximated here as 365/1095 *days*
(the assumption registry's defaults) — calendar-month-exact comparison
would need month/day arithmetic, and the day-count approximation can differ
by at most ~1 day in rare leap-year edge cases. Documented, not hidden.
"""

import enum
from dataclasses import dataclass
from datetime import date

from fundlens.assumptions.service import TaxAssumptions
from fundlens.tax.asset_class import TaxAssetClass


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
    assumptions: TaxAssumptions,
) -> GainClassification:
    if asset_class == TaxAssetClass.NEEDS_REVIEW:
        return GainClassification(
            GainType.UNCLASSIFIED, reason="scheme_category_needs_review"
        )

    if asset_class == TaxAssetClass.EQUITY:
        gain_type = (
            GainType.LTCG
            if holding_period_days > assumptions.equity_ltcg_threshold_days
            else GainType.STCG
        )
        return GainClassification(gain_type)

    # DEBT
    if purchase_date >= assumptions.debt_specified_fund_cutoff:
        return GainClassification(GainType.STCG)

    gain_type = (
        GainType.LTCG
        if holding_period_days > assumptions.debt_ltcg_threshold_days
        else GainType.STCG
    )
    return GainClassification(gain_type)
