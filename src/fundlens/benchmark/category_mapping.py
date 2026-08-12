"""FR-4.3: category-correct default benchmark selection, keyed off the same
real AMFI category taxonomy tax/asset_class.py classifies (e.g. midcap fund
-> Nifty Midcap 150 TRI).

Debt/needs_review categories return None — comparing a debt (or
tax-ambiguous) fund's returns against an equity index would misattribute
category-driven return differences as fund skill, so "no benchmark" is
returned rather than guessed. Category-appropriate debt indices are PRD
FR-1.4-named but not built (compute-engine.md §4) — no debt BenchmarkId
exists yet.
"""

from fundlens.db.models import BenchmarkId
from fundlens.tax.asset_class import TaxAssetClass


def default_benchmark_for_category(
    category_raw: str, tax_asset_class: TaxAssetClass
) -> BenchmarkId | None:
    if tax_asset_class != TaxAssetClass.EQUITY:
        return None

    normalized = category_raw.strip().lower()
    if "mid cap" in normalized and "large" not in normalized:
        return BenchmarkId.NIFTY_MIDCAP150_TRI
    if "small cap" in normalized:
        return BenchmarkId.NIFTY_SMALLCAP250_TRI
    # Large Cap, Large & Mid Cap, Flexi/Multi Cap, ELSS, Focused, Value,
    # Contra, Dividend Yield, Sectoral/Thematic, and any other equity
    # category without a crisper cap-size match: PRD §12.3's universal
    # portfolio-level default.
    return BenchmarkId.NIFTY50_TRI
