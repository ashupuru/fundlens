"""Cost signals for the rubric (PRD §5.3, compute-engine.md §6): the
Direct-vs-Regular flag, and the expense-ratio score alongside it.

**Direct-vs-Regular** flags regular-plan holdings by parsing the scheme
name — AMFI scheme names reliably embed "Direct Plan"/"Regular Plan"
(real-data-verified against the live AMFI feed, e.g. "UTI Nifty 50 Index
Fund - Growth Option- Direct").

**Expense ratio.** This module previously stated that no free expense-ratio
source existed — live-checked during P1.1/P1.3, mfapi.in's scheme metadata
had no TER field and AMFI's TER disclosures were scattered per-AMC filings.
That is no longer true: the Groww screener scraper populates
`groww_fund_metadata.expense_ratio` (3,146 funds as of this change), so the
cost component the rubric had narrowed to a binary flag now has a real
graded number to work with. The commission-drag-in-rupees estimate the
original design wanted is still not computed here — that needs the held
regular plan's *direct-plan twin*, a mapping Groww's direct-plan-only feed
can't supply — so cost remains two signals rather than one rupee figure.

**Scoring is relative to category peers, not an absolute threshold.** An
absolute TER cutoff is meaningless across categories: in our own data
Overnight funds span 0.07-0.20% while Aggressive Hybrid spans 0.07-8.66%.
A percentile rank within the sub-category fixes the scale problem but
introduces a worse one — it amplifies noise in tight distributions, scoring
ICICI Overnight (0.09%) at 58 and HDFC Overnight (0.11%) at 40, inflating a
two-basis-point gap into 18 points of verdict score. So the score is the
gap from the category *median*, mapped the same way `verdict.py` maps alpha:
50 means priced at the median, and +/-1pp of TER versus peers is +/-50
points. Median rather than mean because the source data carries obvious
bad rows (an 8.66% "Aggressive Hybrid") that a mean would chase.

A judgment call on the slope, not a canonical formula — same standing as
alpha's and category_fit's scalings. It was chosen against the real
portfolio: it separates an active Large Cap at 1.04% (score 20) from an
index fund at 0.22% (score 61), while leaving all seven held Overnight
funds at 50-51, which is the honest answer for a category where cost
differences are basis points. A slope of 100 was tested and rejected — it
saturates six held funds at 0 or 100.
"""

import enum
import statistics
from collections.abc import Mapping
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from fundlens.db.models import GrowwFundMetadata

# Below this many priced peers a "median" is not a category norm, it's an
# anecdote — 4 sub-categories in the real feed fall under 10 funds, and 70
# rows carry no sub-category at all. Reported as unscored, never guessed.
MIN_PEERS = 5

# Percentage points of TER difference that move the score a full 50 points.
_SCORE_SLOPE = Decimal(50)
_NEUTRAL_SCORE = Decimal(50)


class PlanType(enum.StrEnum):
    DIRECT = "direct"
    REGULAR = "regular"
    # Genuinely ambiguous (e.g. ETFs trade on-exchange and have no
    # Direct/Regular distinction at all) or the scheme name doesn't say —
    # never guessed either way.
    UNKNOWN = "unknown"


def detect_plan_type(scheme_name: str) -> PlanType:
    normalized = scheme_name.strip().lower()
    if "direct" in normalized:
        return PlanType.DIRECT
    if "regular" in normalized:
        return PlanType.REGULAR
    return PlanType.UNKNOWN


def get_category_expense_medians(session: Session) -> dict[str, Decimal]:
    """Median expense ratio per Groww sub-category, for categories with at
    least `MIN_PEERS` priced funds. Batched deliberately: this is an
    aggregate over the whole scheme universe, so callers scoring many
    holdings compute it once (see `verdict.get_all_verdicts`).
    """
    rows = session.execute(
        select(GrowwFundMetadata.sub_category, GrowwFundMetadata.expense_ratio).where(
            GrowwFundMetadata.expense_ratio.is_not(None),
            GrowwFundMetadata.sub_category.is_not(None),
            GrowwFundMetadata.sub_category != "",
        )
    ).all()

    by_category: dict[str, list[Decimal]] = {}
    for sub_category, expense_ratio in rows:
        by_category.setdefault(sub_category, []).append(expense_ratio)

    return {
        category: statistics.median(ratios)
        for category, ratios in by_category.items()
        if len(ratios) >= MIN_PEERS
    }


def compute_cost_score(
    expense_ratio: Decimal | None,
    sub_category: str | None,
    peer_medians: Mapping[str, Decimal],
    plan_type: PlanType,
) -> tuple[float | None, str | None]:
    """0-100 cost score for one fund, or `(None, reason)` when it can't be
    computed. Mirrors `allocation.compute_category_fit_score`'s shape.
    """
    if expense_ratio is None:
        # Groww's screener is direct-plan-only (data-model.md), so a Regular
        # holding has no row at all. Distinguished from a plain data gap
        # because the cause is structural and permanent, not a fetch that
        # might succeed later — and because `direct_vs_regular` is already
        # scoring exactly this fund 0.
        if plan_type == PlanType.REGULAR:
            return None, "expense_ratio_unavailable_regular_plan"
        return None, "expense_ratio_unavailable"

    if not sub_category:
        return None, "no_peer_category"

    median = peer_medians.get(sub_category)
    if median is None:
        return None, "insufficient_category_peers"

    raw = _NEUTRAL_SCORE + (median - expense_ratio) * _SCORE_SLOPE
    return float(max(Decimal(0), min(Decimal(100), raw))), None
