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
from fundlens.tax.asset_class import EquityCapSize, TaxAssetClass, equity_cap_size


def default_benchmark_for_category(
    category_raw: str, tax_asset_class: TaxAssetClass
) -> BenchmarkId | None:
    if tax_asset_class != TaxAssetClass.EQUITY:
        return None

    cap_size = equity_cap_size(category_raw)
    if cap_size == EquityCapSize.MID:
        return BenchmarkId.NIFTY_MIDCAP150_TRI
    if cap_size == EquityCapSize.SMALL:
        return BenchmarkId.NIFTY_SMALLCAP250_TRI
    # LARGE, LARGE_AND_MID, OTHER: PRD §12.3's universal portfolio-level
    # default — no crisper single-cap index fits any of these.
    return BenchmarkId.NIFTY50_TRI
