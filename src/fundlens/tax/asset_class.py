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
