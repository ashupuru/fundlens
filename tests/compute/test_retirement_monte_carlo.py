"""run_retirement_monte_carlo tests.

Monte Carlo output is statistical, so the correctness gate is a
**zero-volatility collapse**: with volatility 0 every simulated path is
deterministic and must reproduce `compute/retirement.py`'s own terminal
corpus and depletion age *exactly*. That's what stops the vectorized
simulation drifting from the year loop it mirrors -- the two implement the
same year ordering in different code, and only this test holds them
together.
"""

from decimal import Decimal

import numpy as np
import pytest
from sqlalchemy.orm import Session

from fundlens.assumptions.service import GoalAssumptions
from fundlens.compute.monte_carlo import run_retirement_monte_carlo
from fundlens.compute.retirement import compute_retirement_projection
from fundlens.db.models import Persona, RiskProfile

_TOLERANCE_PAISE = 100  # float64 vs Decimal accumulation over ~35 years, on ~Rs 86 Cr


def _profile(**overrides: object) -> RiskProfile:
    defaults: dict[str, object] = {
        "id": 1,
        "persona": Persona.CUSTOM,
        "target_equity_pct": Decimal("100"),
        "target_debt_pct": Decimal("0"),
        "current_age": 50,
        "retirement_age": 60,
        "life_expectancy": 85,
        "annual_investment_paise": 60_000_000,
        "annual_investment_step_up_pct": Decimal("10"),
        "monthly_withdrawal_today_paise": 10_000_000,
        "post_retirement_return_pct": Decimal("8"),
    }
    defaults.update(overrides)
    return RiskProfile(**defaults)  # type: ignore[arg-type]


def _assumptions() -> GoalAssumptions:
    return GoalAssumptions(
        inflation_pct=Decimal("7"),
        expected_equity_return_pct=Decimal("10"),
        expected_debt_return_pct=Decimal("7"),
    )


def _seed_corpus(monkeypatch: pytest.MonkeyPatch, paise: int) -> None:
    from fundlens.compute import retirement as retirement_module
    from fundlens.compute.metrics import PortfolioMetrics

    monkeypatch.setattr(
        retirement_module,
        "compute_portfolio_metrics",
        lambda _session: PortfolioMetrics(
            xirr=None, xirr_reason=None, invested_paise=paise, received_paise=0,
            current_value_paise=paise, positions_missing_nav=0,
        ),
    )


def _run_from(projection: object, profile: RiskProfile, volatility: str) -> object:
    """Feeds the deterministic projection's own resolved figures into the
    simulation, so any divergence is the simulation's, not different inputs."""
    p = projection
    return run_retirement_monte_carlo(
        starting_corpus_paise=p.starting_corpus_paise,  # type: ignore[attr-defined]
        annual_investment_paise=profile.annual_investment_paise or 0,
        step_up_pct=profile.annual_investment_step_up_pct or Decimal(0),
        current_age=p.current_age,  # type: ignore[attr-defined]
        retirement_age=p.retirement_age,  # type: ignore[attr-defined]
        life_expectancy=p.life_expectancy,  # type: ignore[attr-defined]
        first_year_withdrawal_paise=p.first_year_annual_withdrawal_paise,  # type: ignore[attr-defined]
        lump_sum_by_age={},
        pre_return_pct=p.blended_return_pct,  # type: ignore[attr-defined]
        post_return_pct=p.post_retirement_return_pct,  # type: ignore[attr-defined]
        volatility_pct=Decimal(volatility),
        inflation_pct=p.inflation_pct,  # type: ignore[attr-defined]
        rng=np.random.default_rng(42),
    )


def test_zero_volatility_reproduces_the_deterministic_terminal_corpus(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_corpus(monkeypatch, 50_000_000_000)  # Rs 5 Cr -- survives comfortably
    profile = _profile()
    projection = compute_retirement_projection(session, profile, _assumptions())
    assert projection.depletion_age is None

    result = _run_from(projection, profile, "0")

    assert result.success_probability_pct == 100.0  # type: ignore[attr-defined]
    assert result.median_depletion_age is None  # type: ignore[attr-defined]
    # Every path identical, and equal to the deterministic engine's own answer.
    assert (
        abs(
            result.percentile_50_terminal_corpus_paise  # type: ignore[attr-defined]
            - (projection.terminal_corpus_paise or 0)
        )
        < _TOLERANCE_PAISE
    )
    assert (
        result.percentile_10_terminal_corpus_paise  # type: ignore[attr-defined]
        == result.percentile_90_terminal_corpus_paise  # type: ignore[attr-defined]
    )


def test_zero_volatility_reproduces_the_deterministic_depletion_age(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_corpus(monkeypatch, 100_000_000)  # Rs 1 L -- depletes early
    profile = _profile(annual_investment_paise=0, annual_investment_step_up_pct=Decimal("0"))
    projection = compute_retirement_projection(session, profile, _assumptions())
    assert projection.depletion_age is not None

    result = _run_from(projection, profile, "0")

    assert result.success_probability_pct == 0.0  # type: ignore[attr-defined]
    assert result.median_depletion_age == projection.depletion_age  # type: ignore[attr-defined]


def test_volatility_widens_the_outcome_spread(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_corpus(monkeypatch, 50_000_000_000)
    profile = _profile()
    projection = compute_retirement_projection(session, profile, _assumptions())

    calm = _run_from(projection, profile, "5")
    wild = _run_from(projection, profile, "20")

    calm_spread = (
        calm.percentile_90_terminal_corpus_paise - calm.percentile_10_terminal_corpus_paise  # type: ignore[attr-defined]
    )
    wild_spread = (
        wild.percentile_90_terminal_corpus_paise - wild.percentile_10_terminal_corpus_paise  # type: ignore[attr-defined]
    )
    assert wild_spread > calm_spread


def test_sequence_risk_makes_a_deterministically_surviving_plan_fail_sometimes(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The whole point of the feature: a plan the deterministic projection
    calls sustainable is not guaranteed once returns arrive in an order."""
    _seed_corpus(monkeypatch, 5_000_000_000)  # Rs 50 L -- survives on averages, barely
    profile = _profile()
    projection = compute_retirement_projection(session, profile, _assumptions())
    assert projection.depletion_age is None  # deterministically fine

    result = _run_from(projection, profile, "18")

    assert result.success_probability_pct < 100.0  # type: ignore[attr-defined]
    assert result.median_depletion_age is not None  # type: ignore[attr-defined]


def test_same_seed_is_reproducible(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_corpus(monkeypatch, 50_000_000_000)
    profile = _profile()
    projection = compute_retirement_projection(session, profile, _assumptions())

    first = _run_from(projection, profile, "15")
    second = _run_from(projection, profile, "15")

    assert first == second


def test_lump_sum_events_reduce_the_success_probability(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_corpus(monkeypatch, 5_000_000_000)
    profile = _profile()
    projection = compute_retirement_projection(session, profile, _assumptions())

    without = _run_from(projection, profile, "18")

    with_event = run_retirement_monte_carlo(
        starting_corpus_paise=projection.starting_corpus_paise or 0,
        annual_investment_paise=profile.annual_investment_paise or 0,
        step_up_pct=profile.annual_investment_step_up_pct or Decimal(0),
        current_age=projection.current_age or 0,
        retirement_age=projection.retirement_age or 0,
        life_expectancy=projection.life_expectancy or 0,
        first_year_withdrawal_paise=projection.first_year_annual_withdrawal_paise or 0,
        lump_sum_by_age={55: 200_000_000},  # Rs 20 L mid-accumulation
        pre_return_pct=projection.blended_return_pct or Decimal(0),
        post_return_pct=projection.post_retirement_return_pct or Decimal(0),
        volatility_pct=Decimal("18"),
        inflation_pct=projection.inflation_pct or Decimal(0),
        rng=np.random.default_rng(42),
    )

    assert with_event.success_probability_pct < without.success_probability_pct  # type: ignore[attr-defined]
