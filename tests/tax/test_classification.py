from datetime import date

from fundlens.assumptions.service import TaxAssumptions
from fundlens.tax.asset_class import TaxAssetClass
from fundlens.tax.classification import GainType, classify_gain

_ASSUMPTIONS = TaxAssumptions(
    equity_ltcg_threshold_days=365,
    debt_ltcg_threshold_days=1095,
    debt_specified_fund_cutoff=date(2023, 4, 1),
    equity_grandfather_cutoff=date(2018, 1, 31),
    equity_ltcg_exemption_paise_per_fy=12_500_000,
)


def test_equity_held_over_12_months_is_ltcg() -> None:
    result = classify_gain(
        TaxAssetClass.EQUITY, date(2022, 1, 1), holding_period_days=400,
        assumptions=_ASSUMPTIONS,
    )
    assert result.gain_type == GainType.LTCG
    assert result.reason is None


def test_equity_held_exactly_12_months_365_days_is_stcg() -> None:
    """Legal test is "more than 12 months" — exactly at the threshold does
    not qualify as long-term."""
    result = classify_gain(
        TaxAssetClass.EQUITY, date(2022, 1, 1), holding_period_days=365,
        assumptions=_ASSUMPTIONS,
    )
    assert result.gain_type == GainType.STCG


def test_equity_held_under_12_months_is_stcg() -> None:
    result = classify_gain(
        TaxAssetClass.EQUITY, date(2022, 1, 1), holding_period_days=100,
        assumptions=_ASSUMPTIONS,
    )
    assert result.gain_type == GainType.STCG


def test_debt_purchased_after_2023_cutoff_is_always_stcg_regardless_of_holding_period() -> None:
    """Finance Act 2023 'specified mutual fund' rule: no LTCG concept at
    all for debt-oriented funds purchased on/after 1 Apr 2023."""
    result = classify_gain(
        TaxAssetClass.DEBT, date(2023, 4, 1), holding_period_days=2000,
        assumptions=_ASSUMPTIONS,
    )
    assert result.gain_type == GainType.STCG


def test_debt_purchased_exactly_on_cutoff_date_is_specified_fund() -> None:
    result = classify_gain(
        TaxAssetClass.DEBT, date(2023, 4, 1), holding_period_days=1,
        assumptions=_ASSUMPTIONS,
    )
    assert result.gain_type == GainType.STCG


def test_debt_purchased_before_2023_cutoff_uses_36_month_threshold() -> None:
    result = classify_gain(
        TaxAssetClass.DEBT, date(2023, 3, 31), holding_period_days=1200,
        assumptions=_ASSUMPTIONS,
    )
    assert result.gain_type == GainType.LTCG


def test_debt_purchased_before_2023_cutoff_under_36_months_is_stcg() -> None:
    result = classify_gain(
        TaxAssetClass.DEBT, date(2023, 3, 31), holding_period_days=500,
        assumptions=_ASSUMPTIONS,
    )
    assert result.gain_type == GainType.STCG


def test_debt_purchased_before_cutoff_exactly_36_months_1095_days_is_stcg() -> None:
    result = classify_gain(
        TaxAssetClass.DEBT, date(2020, 1, 1), holding_period_days=1095,
        assumptions=_ASSUMPTIONS,
    )
    assert result.gain_type == GainType.STCG


def test_needs_review_asset_class_is_unclassified_not_guessed() -> None:
    result = classify_gain(
        TaxAssetClass.NEEDS_REVIEW, date(2020, 1, 1), holding_period_days=2000,
        assumptions=_ASSUMPTIONS,
    )
    assert result.gain_type == GainType.UNCLASSIFIED
    assert result.reason == "scheme_category_needs_review"


def test_custom_assumptions_change_equity_threshold() -> None:
    custom = TaxAssumptions(
        equity_ltcg_threshold_days=730,  # a hypothetical 24-month threshold
        debt_ltcg_threshold_days=1095,
        debt_specified_fund_cutoff=date(2023, 4, 1),
        equity_grandfather_cutoff=date(2018, 1, 31),
        equity_ltcg_exemption_paise_per_fy=12_500_000,
    )
    result = classify_gain(
        TaxAssetClass.EQUITY, date(2022, 1, 1), holding_period_days=400, assumptions=custom
    )
    assert result.gain_type == GainType.STCG  # 400 days < custom 730-day threshold
