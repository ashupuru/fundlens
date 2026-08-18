"""Age/horizon glide-path suggestions (PRD FR-7.3, phase P3,
compute-engine.md §13): as an investor nears their goal horizon, standard
practice is to gradually shift target allocation from equity-heavy toward
debt-heavy. Advisory only -- never mutates `RiskProfile` automatically;
the user acts on it via the existing `POST /risk-profile` if they choose
to, same as every other suggestion in this app (verdicts, alternatives).

`RiskProfile` has no birthdate or fixed target date, only `horizon_years`
(set once at onboarding) and `updated_at` -- so "years remaining" is
derived as `horizon_years - (years since updated_at)`, not read directly.
Known, disclosed limitation: this only counts down correctly if the user
doesn't touch other RiskProfile fields without also updating
`horizon_years`; there's no fixed target date to anchor against instead.
"""

from dataclasses import dataclass
from datetime import UTC, datetime

from fundlens.db.models import RiskProfile

_GLIDE_START_YEARS = 15.0  # years remaining at which glide-down begins
_EQUITY_FLOOR_PCT = 20.0  # never suggest going below this, even at/past the horizon
_MIN_MEANINGFUL_DRIFT_PP = 2.0  # suppress a "suggestion" for a trivial difference


@dataclass(frozen=True)
class GlidePathSuggestion:
    years_remaining: float | None
    current_target_equity_pct: float
    current_target_debt_pct: float
    suggested_target_equity_pct: float | None
    suggested_target_debt_pct: float | None
    drift_pp: float | None  # suggested - current; negative = suggests reducing equity
    reason: str | None  # "no_horizon_set", or None when a suggestion was computed


def _years_remaining(profile: RiskProfile) -> float | None:
    if profile.horizon_years is None:
        return None
    now = datetime.now(UTC).replace(tzinfo=None)
    years_elapsed = (now - profile.updated_at).days / 365.0
    return profile.horizon_years - years_elapsed


def compute_glide_path_suggestion(profile: RiskProfile) -> GlidePathSuggestion:
    current_equity = float(profile.target_equity_pct)
    current_debt = float(profile.target_debt_pct)

    years_remaining = _years_remaining(profile)
    if years_remaining is None:
        return GlidePathSuggestion(
            years_remaining=None,
            current_target_equity_pct=current_equity,
            current_target_debt_pct=current_debt,
            suggested_target_equity_pct=None,
            suggested_target_debt_pct=None,
            drift_pp=None,
            reason="no_horizon_set",
        )

    if years_remaining >= _GLIDE_START_YEARS:
        suggested_equity = current_equity
    elif years_remaining <= 0:
        suggested_equity = _EQUITY_FLOOR_PCT
    else:
        suggested_equity = _EQUITY_FLOOR_PCT + (current_equity - _EQUITY_FLOOR_PCT) * (
            years_remaining / _GLIDE_START_YEARS
        )
    # Guards an edge case the interpolation formula doesn't handle on its own:
    # if current_equity is already below the floor (an unusually conservative
    # custom profile), the interpolation would compute a value BETWEEN
    # current and the floor as years_remaining shrinks -- i.e. suggest
    # *increasing* equity while approaching the horizon, backwards for a
    # glide path. Never suggest more equity than the current target.
    suggested_equity = min(suggested_equity, current_equity)

    drift_pp = suggested_equity - current_equity
    if abs(drift_pp) < _MIN_MEANINGFUL_DRIFT_PP:
        return GlidePathSuggestion(
            years_remaining=years_remaining,
            current_target_equity_pct=current_equity,
            current_target_debt_pct=current_debt,
            suggested_target_equity_pct=current_equity,
            suggested_target_debt_pct=current_debt,
            drift_pp=0.0,
            reason=None,
        )

    suggested_debt = current_debt + (current_equity - suggested_equity)
    return GlidePathSuggestion(
        years_remaining=years_remaining,
        current_target_equity_pct=current_equity,
        current_target_debt_pct=current_debt,
        suggested_target_equity_pct=suggested_equity,
        suggested_target_debt_pct=suggested_debt,
        drift_pp=drift_pp,
        reason=None,
    )
