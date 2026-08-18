"""compute_glide_path_suggestion tests. Each case's expected suggested
equity % is computed independently (plain Python, not calling into the
module under test) from the same linear-interpolation formula, matching
the cross-check convention used throughout this session's P2/P3 work.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from fundlens.compute.glide_path import compute_glide_path_suggestion
from fundlens.db.models import Persona, RiskProfile

_FLOOR = 20.0
_START = 15.0
_TOLERANCE = 1e-6


def _make_profile(
    target_equity_pct: str, target_debt_pct: str, horizon_years: int | None, years_ago: float
) -> RiskProfile:
    return RiskProfile(
        id=1,
        persona=Persona.CUSTOM,
        target_equity_pct=Decimal(target_equity_pct),
        target_debt_pct=Decimal(target_debt_pct),
        horizon_years=horizon_years,
        updated_at=datetime.now(UTC).replace(tzinfo=None) - timedelta(days=years_ago * 365),
    )


def test_no_horizon_set() -> None:
    profile = _make_profile("80", "20", horizon_years=None, years_ago=0)

    suggestion = compute_glide_path_suggestion(profile)

    assert suggestion.years_remaining is None
    assert suggestion.reason == "no_horizon_set"
    assert suggestion.suggested_target_equity_pct is None


def test_years_remaining_well_beyond_glide_start_suggests_no_change() -> None:
    profile = _make_profile("80", "20", horizon_years=20, years_ago=0)

    suggestion = compute_glide_path_suggestion(profile)

    assert suggestion.years_remaining is not None
    assert suggestion.years_remaining >= _START
    assert suggestion.suggested_target_equity_pct == 80.0
    assert suggestion.drift_pp == 0.0
    assert suggestion.reason is None


def test_years_remaining_past_horizon_suggests_floor() -> None:
    # horizon_years=5, set 6 years ago -> years_remaining = 5 - 6 = -1.
    profile = _make_profile("80", "20", horizon_years=5, years_ago=6)

    suggestion = compute_glide_path_suggestion(profile)

    assert suggestion.years_remaining is not None and suggestion.years_remaining < 0
    assert suggestion.suggested_target_equity_pct == _FLOOR
    assert suggestion.suggested_target_debt_pct == 80.0  # 20 + (80 - 20)


def test_interpolation_hand_computed() -> None:
    # horizon_years=10, set 4 years ago -> years_remaining = 6.
    profile = _make_profile("80", "20", horizon_years=10, years_ago=4)

    suggestion = compute_glide_path_suggestion(profile)

    assert suggestion.years_remaining is not None
    assert abs(suggestion.years_remaining - 6.0) < 0.02  # 4*365 day rounding

    years_remaining = suggestion.years_remaining
    expected_equity = _FLOOR + (80.0 - _FLOOR) * (years_remaining / _START)
    assert suggestion.suggested_target_equity_pct is not None
    assert abs(suggestion.suggested_target_equity_pct - expected_equity) < _TOLERANCE
    expected_drift = expected_equity - 80.0
    assert suggestion.drift_pp is not None
    assert abs(suggestion.drift_pp - expected_drift) < _TOLERANCE


def test_small_drift_suppressed_as_on_track() -> None:
    # horizon_years=15, set ~0.5yr ago -> years_remaining ~= 14.5, close enough
    # to the glide-start boundary that the suggested equity is within 2pp of
    # current (60%) -- suppressed, not surfaced as an actionable suggestion.
    profile = _make_profile("60", "40", horizon_years=15, years_ago=0.5)

    suggestion = compute_glide_path_suggestion(profile)

    assert suggestion.reason is None
    assert suggestion.drift_pp == 0.0
    assert suggestion.suggested_target_equity_pct == 60.0


def test_glide_path_never_suggests_increasing_equity() -> None:
    # current_equity (15%) already below the floor (20%) -- an unusually
    # conservative custom profile. The interpolation would otherwise compute
    # a value BETWEEN 15 and 20 as years_remaining shrinks, i.e. suggest
    # *more* equity approaching the horizon. Must never happen.
    profile = _make_profile("15", "85", horizon_years=10, years_ago=4)

    suggestion = compute_glide_path_suggestion(profile)

    assert suggestion.suggested_target_equity_pct is not None
    assert suggestion.suggested_target_equity_pct <= 15.0
