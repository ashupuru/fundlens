from datetime import datetime
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from fundlens.compute.plan_audit import (
    MIN_PEERS,
    PlanType,
    compute_cost_score,
    detect_plan_type,
    get_category_expense_medians,
)
from fundlens.db.models import GrowwFundMetadata


@pytest.mark.parametrize(
    "scheme_name",
    [
        "UTI Nifty 50 Index Fund - Growth Option- Direct",
        "Motilal Oswal Nifty Midcap 150 Index Fund - Direct Plan",
        "HDFC Flexi Cap Fund - Direct Plan - Growth",
    ],
)
def test_detects_direct_plan(scheme_name: str) -> None:
    assert detect_plan_type(scheme_name) == PlanType.DIRECT


@pytest.mark.parametrize(
    "scheme_name",
    [
        "UTI Nifty 50 Index Fund - Regular Plan - Growth Option",
        "HDFC Flexi Cap Fund - Regular Plan - Growth",
    ],
)
def test_detects_regular_plan(scheme_name: str) -> None:
    assert detect_plan_type(scheme_name) == PlanType.REGULAR


def test_unknown_when_neither_marker_present() -> None:
    assert detect_plan_type("SBI BSE 100 ETF") == PlanType.UNKNOWN


# --- expense-ratio cost score -------------------------------------------------
#
# Figures are the real ones from the portfolio this was designed against:
# Flexi Cap's median sits at 0.91% and Overnight's at 0.11%, and the whole
# Overnight category spans 13 basis points.

_FLEXI_MEDIANS = {"Flexi Cap": Decimal("0.91")}


def _seed_groww(
    session: Session, *, amfi_code: str, sub_category: str | None, expense_ratio: str | None
) -> None:
    session.add(
        GrowwFundMetadata(
            amfi_code=amfi_code,
            scheme_name=f"Fund {amfi_code}",
            sub_category=sub_category,
            expense_ratio=Decimal(expense_ratio) if expense_ratio is not None else None,
            fetched_at=datetime(2026, 8, 19, 12, 0, 0),
        )
    )


def test_priced_at_the_category_median_scores_neutral() -> None:
    score, reason = compute_cost_score(
        Decimal("0.91"), "Flexi Cap", _FLEXI_MEDIANS, PlanType.DIRECT
    )
    assert score == 50.0
    assert reason is None


def test_cheaper_than_peers_scores_above_neutral() -> None:
    # HDFC Flexi Cap's real 0.74% against a 0.91% median.
    score, _ = compute_cost_score(Decimal("0.74"), "Flexi Cap", _FLEXI_MEDIANS, PlanType.DIRECT)
    assert score == pytest.approx(58.5)


def test_costlier_than_peers_scores_below_neutral() -> None:
    score, _ = compute_cost_score(Decimal("1.21"), "Flexi Cap", _FLEXI_MEDIANS, PlanType.DIRECT)
    assert score == pytest.approx(35.0)


@pytest.mark.parametrize(
    ("expense_ratio", "expected"),
    # Needs a wide category to reach either bound: at a 2.00% median a free
    # fund saturates at 100, and a 5% one bottoms out at 0.
    [("0.00", 100.0), ("5.00", 0.0)],
)
def test_score_is_clamped_to_the_0_100_bounds(expense_ratio: str, expected: float) -> None:
    score, _ = compute_cost_score(
        Decimal(expense_ratio), "Wide", {"Wide": Decimal("2.00")}, PlanType.DIRECT
    )
    assert score == expected


def test_a_tight_category_stays_near_neutral_for_every_fund() -> None:
    """The property that ruled out percentile ranking. The entire Overnight
    category spans 13 basis points, so cost genuinely does not discriminate
    within it. Percentile ranking spreads those same funds across 40-58 --
    manufacturing an 18-point signal out of rounding -- while the median-gap
    mapping keeps the whole category inside a 7-point band around neutral.
    """
    medians = {"Overnight": Decimal("0.11")}
    scores = [
        compute_cost_score(Decimal(er), "Overnight", medians, PlanType.DIRECT)[0]
        for er in ("0.07", "0.087", "0.09", "0.10", "0.11", "0.20")
    ]

    assert all(s is not None for s in scores)
    assert max(scores) - min(scores) < 7.0  # type: ignore[operator]
    assert all(45.0 <= s <= 53.0 for s in scores)  # type: ignore[operator]


def test_missing_expense_ratio_is_unscored_not_zero() -> None:
    score, reason = compute_cost_score(None, "Flexi Cap", _FLEXI_MEDIANS, PlanType.DIRECT)
    assert score is None
    assert reason == "expense_ratio_unavailable"


def test_regular_plan_gets_its_own_reason() -> None:
    """Groww's screener is direct-plan-only, so a Regular holding is missing
    structurally rather than transiently -- and `direct_vs_regular` is
    already scoring it 0."""
    score, reason = compute_cost_score(None, None, _FLEXI_MEDIANS, PlanType.REGULAR)
    assert score is None
    assert reason == "expense_ratio_unavailable_regular_plan"


def test_no_sub_category_means_no_peer_group() -> None:
    score, reason = compute_cost_score(Decimal("0.74"), None, _FLEXI_MEDIANS, PlanType.DIRECT)
    assert score is None
    assert reason == "no_peer_category"


def test_category_absent_from_medians_is_reported_as_thin_peers() -> None:
    score, reason = compute_cost_score(Decimal("0.74"), "Gold", _FLEXI_MEDIANS, PlanType.DIRECT)
    assert score is None
    assert reason == "insufficient_category_peers"


def test_medians_exclude_categories_below_the_peer_floor(session: Session) -> None:
    for i in range(MIN_PEERS):
        _seed_groww(session, amfi_code=f"B{i}", sub_category="Big", expense_ratio="1.0")
    for i in range(MIN_PEERS - 1):
        _seed_groww(session, amfi_code=f"S{i}", sub_category="Small", expense_ratio="1.0")
    session.commit()

    medians = get_category_expense_medians(session)

    assert "Big" in medians
    assert "Small" not in medians


def test_median_ignores_an_outlier_a_mean_would_chase(session: Session) -> None:
    """The source feed carries obviously-bad rows -- a real 8.66% "Aggressive
    Hybrid" among peers averaging ~1.1%."""
    for i, er in enumerate(("0.60", "0.70", "0.80", "0.90", "8.66")):
        _seed_groww(session, amfi_code=f"A{i}", sub_category="Aggressive Hybrid", expense_ratio=er)
    session.commit()

    medians = get_category_expense_medians(session)

    assert medians["Aggressive Hybrid"] == Decimal("0.80")


def test_rows_without_a_sub_category_or_ratio_are_skipped(session: Session) -> None:
    for i in range(MIN_PEERS):
        _seed_groww(session, amfi_code=f"K{i}", sub_category="Kept", expense_ratio="0.5")
    _seed_groww(session, amfi_code="N1", sub_category=None, expense_ratio="0.5")
    _seed_groww(session, amfi_code="N2", sub_category="", expense_ratio="0.5")
    _seed_groww(session, amfi_code="N3", sub_category="Kept", expense_ratio=None)
    session.commit()

    medians = get_category_expense_medians(session)

    assert set(medians) == {"Kept"}
