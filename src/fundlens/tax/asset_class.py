"""Classifies an AMFI scheme category string into the tax-relevant asset
class (PRD FR-10.2, compute-engine.md §3.1).

Grounded in the complete, real set of category header strings AMFI NAVAll
actually publishes (fetched and enumerated live, 2026-08 — see the pattern
groups below, which are a literal transcription of what was observed, not
guessed from general knowledge of SEBI categories).

Indian capital-gains tax turns on a specific legal test — Section 2(42A)'s
"equity oriented fund" requires >65% investment in equity shares of DOMESTIC
companies — not a general "is this fund stocky" judgment call. Categories
are bucketed EQUITY / DEBT only where that test is unambiguous from the
category name alone:

- EQUITY: plain equity categories, Aggressive Hybrid (65-80% equity),
  Arbitrage Fund (hedged near-zero net equity, but maintains >=65% *gross*
  equity exposure — well-established as equity-oriented for tax purposes
  despite the hedge), equity index/ETF.
- DEBT: plain debt categories, Conservative Hybrid (10-25% equity, clearly
  not equity-oriented), debt index/ETF, and — per the same domestic-company
  test — anything investing overseas (an overseas fund-of-funds can never
  be "equity oriented" under Indian tax law regardless of what its
  underlying holds, since the underlying isn't domestic equity).
- NEEDS_REVIEW: everything whose actual equity allocation isn't determinable
  from the category name — Balanced Hybrid (typically 40-60%, sits in the
  ambiguous zone), Dynamic Asset Allocation/Balanced Advantage and Multi
  Asset (allocation varies, sometimes 0-100%), Equity Savings (nuanced mix
  of equity/arbitrage/debt), domestic fund-of-funds (depends entirely on
  the underlying fund), gold/silver ETFs (a distinct asset class with its
  own rules, not silently folded into "debt"), Solution Oriented and Life
  Cycle funds (allocation varies by scheme/tenor), and the old pre-2018
  bare "Growth"/"Income" category names (too ambiguous to guess). Reported
  as unclassified in the capital gains report rather than silently taxed
  either way — an incorrect equity/debt call changes the tax owed.
"""

import enum


class TaxAssetClass(enum.StrEnum):
    EQUITY = "equity"
    DEBT = "debt"
    NEEDS_REVIEW = "needs_review"


_EQUITY_MARKERS = (
    "equity scheme",
    "equity schemes",
    "aggressive hybrid",
    "arbitrage fund",
    "index funds - equity",
    "(etfs) - equity etf",
    "close ended schemes(elss)",
)

_DEBT_MARKERS = (
    "debt scheme",
    "conservative hybrid",
    "index funds - debt",
    "(etfs) - debt etf",
    "overseas fund of funds",
    "fof overseas",
    "gilt",
    "money market",
    "income",  # covers "Income", "Income/Debt Oriented Schemes - *" — no
    # equity category contains "income" as a substring (checked against the
    # full real taxonomy, tests/tax/test_asset_class.py).
)

# Explicit, not inferred: everything else falls through to NEEDS_REVIEW by
# default (see classify_category), but listing the known ambiguous cases
# here documents that they were considered, not missed.
_KNOWN_NEEDS_REVIEW_MARKERS = (
    "balanced hybrid",
    "balanced advantage",
    "dynamic asset allocation",
    "equity savings",
    "multi asset allocation",
    "fund of funds scheme (domestic)",
    "fof domestic",
    "gold etf",
    "silver etf",
    "other etf",
    "other scheme - index funds",
    "solution oriented",
    "life cycle",
)


def classify_category(category_raw: str) -> TaxAssetClass:
    normalized = category_raw.strip().lower()

    for marker in _EQUITY_MARKERS:
        if marker in normalized:
            return TaxAssetClass.EQUITY

    for marker in _DEBT_MARKERS:
        if marker in normalized:
            return TaxAssetClass.DEBT

    # Covers both the explicitly-known ambiguous markers above and anything
    # genuinely unrecognized — either way, "don't know" beats "guessed wrong".
    return TaxAssetClass.NEEDS_REVIEW


class EquityCapSize(enum.StrEnum):
    """Cap-size bucket within EQUITY-classified categories only — callers
    must check `classify_category(...) == TaxAssetClass.EQUITY` first; this
    function doesn't itself distinguish equity from debt/needs_review."""

    LARGE = "large"
    MID = "mid"
    SMALL = "small"
    LARGE_AND_MID = "large_and_mid"
    # Flexi/Multi Cap, ELSS, Focused, Value, Contra, Dividend Yield,
    # Sectoral/Thematic, equity index/ETF, arbitrage, aggressive hybrid —
    # real equity categories with no single cap-size tilt to report.
    OTHER = "other"


def parse_category_raw(category_raw: str) -> tuple[str, str]:
    """Best-effort split of AMFI's raw category string into (broad
    category, specific type) labels -- e.g. "Open Ended Schemes(Equity
    Scheme - Small Cap Fund)" -> ("Equity", "Small Cap Fund") -- matching
    the shape of Groww's own category/sub_category fields
    (market_data/groww_service.py) so AMFI's classification (available for
    every scheme this app has ever seen a category feed for) can stand in
    when Groww's screener doesn't cover a scheme. Groww's screener is
    direct-plan-only, so any Regular-plan holding is otherwise invisible to
    it entirely, real-data-verified: none of a real portfolio's Regular
    plans had a groww_fund_metadata row despite every one of them having a
    scheme_metadata row via the always-available AMFI feed.

    AMFI's raw strings aren't perfectly uniform (a handful of categories
    are missing their closing paren, e.g. "...(ETFs)"; the pre-2018
    bare names have no " - " separator at all) -- this degrades to using
    the whole cleaned string as both labels on anything that doesn't fit
    the common "(Group Scheme - Specific Type)" pattern, never raises.
    """
    if "(" not in category_raw:
        cleaned = category_raw.strip()
        return cleaned, cleaned

    inner = category_raw.split("(", 1)[1]
    if inner.endswith(")"):
        inner = inner[:-1]
    inner = inner.strip()
    if not inner:
        cleaned = category_raw.strip()
        return cleaned, cleaned

    if " - " in inner:
        broad, specific = inner.split(" - ", 1)
        broad = broad.strip()
        for suffix in ("Schemes", "Scheme"):
            if broad.endswith(suffix):
                broad = broad[: -len(suffix)].strip()
                break
        return (broad or inner), specific.strip()

    return inner, inner


def equity_cap_size(category_raw: str) -> EquityCapSize:
    normalized = category_raw.strip().lower()
    if "large & mid cap" in normalized or "large and mid cap" in normalized:
        return EquityCapSize.LARGE_AND_MID
    if "large cap" in normalized:
        return EquityCapSize.LARGE
    if "mid cap" in normalized:
        return EquityCapSize.MID
    if "small cap" in normalized:
        return EquityCapSize.SMALL
    return EquityCapSize.OTHER
