import pytest

from fundlens.benchmark.category_mapping import default_benchmark_for_category
from fundlens.db.models import BenchmarkId
from fundlens.tax.asset_class import TaxAssetClass

# Real category strings from the live AMFI taxonomy (tests/tax/test_asset_class.py).
LARGE_CAP_CATEGORIES = [
    "Open Ended Schemes(Equity Scheme - Large Cap Fund)",
    "Open Ended Schemes(Equity Schemes - Large Cap Fund)",
]
MID_CAP_CATEGORIES = [
    "Open Ended Schemes(Equity Scheme - Mid Cap Fund)",
    "Open Ended Schemes(Equity Schemes - Mid Cap Fund)",
]
SMALL_CAP_CATEGORIES = [
    "Open Ended Schemes(Equity Scheme - Small Cap Fund)",
    "Open Ended Schemes(Equity Schemes - Small Cap Fund)",
]
LARGE_AND_MID_CAP_CATEGORIES = [
    "Open Ended Schemes(Equity Scheme - Large & Mid Cap Fund)",
    "Open Ended Schemes(Equity Schemes - Large & Mid Cap Fund)",
]
OTHER_EQUITY_CATEGORIES = [
    "Open Ended Schemes(Equity Scheme - Flexi Cap Fund)",
    "Open Ended Schemes(Equity Scheme - Multi Cap Fund)",
    "Open Ended Schemes(Equity Scheme - ELSS)",
    "Open Ended Schemes(Equity Scheme - Focused Fund)",
    "Open Ended Schemes(Equity Scheme - Value Fund)",
    "Open Ended Schemes(Equity Scheme - Contra Fund)",
    "Open Ended Schemes(Equity Scheme - Dividend Yield Fund)",
    "Open Ended Schemes(Equity Scheme - Sectoral/ Thematic)",
]


@pytest.mark.parametrize("category", LARGE_CAP_CATEGORIES)
def test_large_cap_defaults_to_nifty50(category: str) -> None:
    assert (
        default_benchmark_for_category(category, TaxAssetClass.EQUITY) == BenchmarkId.NIFTY50_TRI
    )


@pytest.mark.parametrize("category", MID_CAP_CATEGORIES)
def test_mid_cap_defaults_to_midcap150(category: str) -> None:
    result = default_benchmark_for_category(category, TaxAssetClass.EQUITY)
    assert result == BenchmarkId.NIFTY_MIDCAP150_TRI


@pytest.mark.parametrize("category", SMALL_CAP_CATEGORIES)
def test_small_cap_defaults_to_smallcap250(category: str) -> None:
    result = default_benchmark_for_category(category, TaxAssetClass.EQUITY)
    assert result == BenchmarkId.NIFTY_SMALLCAP250_TRI


@pytest.mark.parametrize("category", LARGE_AND_MID_CAP_CATEGORIES)
def test_large_and_mid_cap_defaults_to_nifty50_not_midcap(category: str) -> None:
    # "mid cap" is a substring of "large & mid cap", but this spans both caps
    # so no single-cap index fits — falls to the portfolio-level default.
    result = default_benchmark_for_category(category, TaxAssetClass.EQUITY)
    assert result == BenchmarkId.NIFTY50_TRI


@pytest.mark.parametrize("category", OTHER_EQUITY_CATEGORIES)
def test_other_equity_categories_default_to_nifty50(category: str) -> None:
    result = default_benchmark_for_category(category, TaxAssetClass.EQUITY)
    assert result == BenchmarkId.NIFTY50_TRI


def test_debt_asset_class_has_no_benchmark() -> None:
    result = default_benchmark_for_category(
        "Open Ended Schemes(Debt Scheme - Liquid Fund)", TaxAssetClass.DEBT
    )
    assert result is None


def test_needs_review_asset_class_has_no_benchmark() -> None:
    result = default_benchmark_for_category(
        "Open Ended Schemes(Hybrid Scheme - Balanced Hybrid Fund)", TaxAssetClass.NEEDS_REVIEW
    )
    assert result is None
