"""Exhaustive classification test against the complete, real set of AMFI
NAVAll category strings (fetched live, 2026-08 — see asset_class.py's
module docstring). Every string below was actually observed in the feed,
not invented."""

import pytest

from fundlens.tax.asset_class import TaxAssetClass, classify_category

EQUITY_CATEGORIES = [
    "Open Ended Schemes(Equity Scheme - Contra Fund)",
    "Open Ended Schemes(Equity Scheme - Dividend Yield Fund)",
    "Open Ended Schemes(Equity Scheme - ELSS)",
    "Open Ended Schemes(Equity Scheme - Flexi Cap Fund)",
    "Open Ended Schemes(Equity Scheme - Focused Fund)",
    "Open Ended Schemes(Equity Scheme - Large & Mid Cap Fund)",
    "Open Ended Schemes(Equity Scheme - Large Cap Fund)",
    "Open Ended Schemes(Equity Scheme - Mid Cap Fund)",
    "Open Ended Schemes(Equity Scheme - Multi Cap Fund)",
    "Open Ended Schemes(Equity Scheme - Sectoral/ Thematic)",
    "Open Ended Schemes(Equity Scheme - Small Cap Fund)",
    "Open Ended Schemes(Equity Scheme - Value Fund)",
    "Open Ended Schemes(Equity Schemes - Contra Fund)",
    "Open Ended Schemes(Equity Schemes - ELSS- Tax Saver Fund)",
    "Open Ended Schemes(Equity Schemes - Flexi Cap Fund)",
    "Open Ended Schemes(Equity Schemes - Focused Fund)",
    "Open Ended Schemes(Equity Schemes - Large & Mid Cap Fund)",
    "Open Ended Schemes(Equity Schemes - Large Cap Fund)",
    "Open Ended Schemes(Equity Schemes - Mid Cap Fund)",
    "Open Ended Schemes(Equity Schemes - Multi Cap Fund)",
    "Open Ended Schemes(Equity Schemes - Sectoral Fund)",
    "Open Ended Schemes(Equity Schemes - Small Cap Fund)",
    "Open Ended Schemes(Equity Schemes - Thematic Fund)",
    "Open Ended Schemes(Equity Schemes - Value Fund)",
    "Open Ended Schemes(Exchange Traded Funds (ETFs) - Equity ETF)",
    "Open Ended Schemes(Hybrid Scheme - Aggressive Hybrid Fund)",
    "Open Ended Schemes(Hybrid Scheme - Arbitrage Fund)",
    "Open Ended Schemes(Hybrid Schemes - Aggressive Hybrid Fund)",
    "Open Ended Schemes(Hybrid Schemes - Arbitrage Fund)",
    "Open Ended Schemes(Index Funds - Equity Funds)",
    "Close Ended Schemes(ELSS)",
]

DEBT_CATEGORIES = [
    "Open Ended Schemes(Debt Scheme - Banking and PSU Fund)",
    "Open Ended Schemes(Debt Scheme - Corporate Bond Fund)",
    "Open Ended Schemes(Debt Scheme - Credit Risk Fund)",
    "Open Ended Schemes(Debt Scheme - Dynamic Bond)",
    "Open Ended Schemes(Debt Scheme - Floater Fund)",
    "Open Ended Schemes(Debt Scheme - Gilt Fund with 10 year constant duration)",
    "Open Ended Schemes(Debt Scheme - Gilt Fund)",
    "Open Ended Schemes(Debt Scheme - Liquid Fund)",
    "Open Ended Schemes(Debt Scheme - Long Duration Fund)",
    "Open Ended Schemes(Debt Scheme - Low Duration Fund)",
    "Open Ended Schemes(Debt Scheme - Medium Duration Fund)",
    "Open Ended Schemes(Debt Scheme - Medium to Long Duration Fund)",
    "Open Ended Schemes(Debt Scheme - Money Market Fund)",
    "Open Ended Schemes(Debt Scheme - Overnight Fund)",
    "Open Ended Schemes(Debt Scheme - Short Duration Fund)",
    "Open Ended Schemes(Debt Scheme - Ultra Short Duration Fund)",
    "Open Ended Schemes(Exchange Traded Funds (ETFs) - Debt ETF)",
    "Open Ended Schemes(Gilt)",
    "Open Ended Schemes(Hybrid Scheme - Conservative Hybrid Fund)",
    "Open Ended Schemes(Hybrid Schemes - Conservative Hybrid Fund)",
    "Open Ended Schemes(Income/Debt Oriented Schemes - Banking and PSU Debt Fund)",
    "Open Ended Schemes(Income/Debt Oriented Schemes - Corporate Bond Fund)",
    "Open Ended Schemes(Income/Debt Oriented Schemes - Credit Risk Fund)",
    "Open Ended Schemes(Income/Debt Oriented Schemes - Dynamic Term Fund)",
    "Open Ended Schemes(Income/Debt Oriented Schemes - Gilt Fund)",
    "Open Ended Schemes(Income/Debt Oriented Schemes - Liquid Fund)",
    "Open Ended Schemes(Income/Debt Oriented Schemes - Medium Term Fund)",
    "Open Ended Schemes(Income/Debt Oriented Schemes - Money Market Fund)",
    "Open Ended Schemes(Income/Debt Oriented Schemes - Overnight Fund)",
    "Open Ended Schemes(Income/Debt Oriented Schemes - Short Term Fund)",
    "Open Ended Schemes(Income/Debt Oriented Schemes - Ultra Short Term Fund)",
    "Open Ended Schemes(Income/Debt Oriented Schemes - Ultra Short to Short Term Fund)",
    "Open Ended Schemes(Index Funds - Debt Funds)",
    "Open Ended Schemes(Money Market)",
    "Open Ended Schemes(Overseas Fund of Funds - Fund of Funds investing overseas)",
    "Open Ended Schemes(Other Scheme - FoF Overseas)",
    "Open Ended Schemes(Income)",
    "Close Ended Schemes(Income)",
    "Interval Fund Schemes(Income)",
]

NEEDS_REVIEW_CATEGORIES = [
    "Open Ended Schemes(Hybrid Scheme - Balanced Hybrid Fund)",
    "Open Ended Schemes(Hybrid Scheme - Dynamic Asset Allocation or Balanced Advantage)",
    "Open Ended Schemes(Hybrid Scheme - Equity Savings)",
    "Open Ended Schemes(Hybrid Scheme - Multi Asset Allocation)",
    "Open Ended Schemes(Hybrid Schemes - Balanced Advantage Fund/ Dynamic Asset Allocation)",
    "Open Ended Schemes(Hybrid Schemes - Equity Savings Fund)",
    "Open Ended Schemes(Hybrid Schemes - Multi Asset Allocation Fund)",
    "Open Ended Schemes(Fund of Funds Scheme (Domestic) - Fund of Funds Scheme (Domestic))",
    "Open Ended Schemes(Other Scheme - FoF Domestic)",
    "Open Ended Schemes(Other Scheme - Gold ETF)",
    "Open Ended Schemes(Other Scheme - Index Funds)",
    "Open Ended Schemes(Other Scheme - Other  ETFs)",
    "Open Ended Schemes(Exchange Traded Funds (ETFs) - Gold ETF)",
    "Open Ended Schemes(Exchange Traded Funds (ETFs) - Silver ETF)",
    "Open Ended Schemes(Exchange Traded Funds (ETFs) - Other ETF)",
    "Open Ended Schemes(Solution Oriented Scheme - Children’s Fund)",
    "Open Ended Schemes(Solution Oriented Scheme - Retirement Fund)",
    "Open Ended Schemes(Life Cycle Funds - Life Cycle Fund with Maturity of 10 Years)",
    "Open Ended Schemes(Life Cycle Funds - Life Cycle Fund with Maturity of 15 Years)",
    "Open Ended Schemes(Growth)",
    "Close Ended Schemes(Growth)",
]


@pytest.mark.parametrize("category", EQUITY_CATEGORIES)
def test_real_equity_categories_classify_as_equity(category: str) -> None:
    assert classify_category(category) == TaxAssetClass.EQUITY


@pytest.mark.parametrize("category", DEBT_CATEGORIES)
def test_real_debt_categories_classify_as_debt(category: str) -> None:
    assert classify_category(category) == TaxAssetClass.DEBT


@pytest.mark.parametrize("category", NEEDS_REVIEW_CATEGORIES)
def test_ambiguous_real_categories_classify_as_needs_review(category: str) -> None:
    assert classify_category(category) == TaxAssetClass.NEEDS_REVIEW


def test_unrecognized_category_defaults_to_needs_review_not_a_guess() -> None:
    result = classify_category("Some Brand New Category Nobody Has Seen")
    assert result == TaxAssetClass.NEEDS_REVIEW


def test_classification_is_case_insensitive() -> None:
    assert classify_category("EQUITY SCHEME - LARGE CAP FUND") == TaxAssetClass.EQUITY
    assert classify_category("debt scheme - liquid fund") == TaxAssetClass.DEBT
