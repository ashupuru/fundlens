"""run_monte_carlo tests. Since Monte Carlo results are inherently
statistical (not hand-computable to one precise value), the correctness
gate here is a zero-volatility collapse test: with equity/debt volatility
both 0, every simulated path is deterministic and must converge to
*exactly* the same figure the module's own monthly-compounding formula
would produce for a clean, integer-years fixture -- a real proof the
compounding math is right, not just "looks statistically plausible."
"""

from decimal import Decimal

import numpy as np

from fundlens.compute.monte_carlo import run_monte_carlo

_TOLERANCE_PAISE = 1  # float rounding, not a real discrepancy


def test_zero_volatility_no_contribution_matches_annual_compounding_exactly() -> None:
    current_corpus_paise = 10_000_000  # Rs 100,000
    blended_return_pct = Decimal("10")
    years = 5.0  # integer years -> n_months = 60 exactly, no rounding drift

    # current_corpus_paise * 1.10^5 =~ Rs 161,051 -- set the target just
    # below that so the deterministic zero-vol outcome is a clean success,
    # and just above it in the second call so it's a clean failure. Proves
    # the simulation's actual compounded output crosses the threshold at
    # the mathematically correct point, not just "some number close to it."
    expected_final = current_corpus_paise * 1.10**years

    just_below = run_monte_carlo(
        goal_id="g1",
        current_corpus_paise=current_corpus_paise,
        inflated_target_paise=round(expected_final - 100),
        required_monthly_sip_paise=0,
        blended_return_pct=blended_return_pct,
        years=years,
        equity_volatility_pct=Decimal("0"),
        debt_volatility_pct=Decimal("0"),
        target_equity_pct=Decimal("100"),
        target_debt_pct=Decimal("0"),
        rng=np.random.default_rng(42),
    )
    just_above = run_monte_carlo(
        goal_id="g1",
        current_corpus_paise=current_corpus_paise,
        inflated_target_paise=round(expected_final + 100),
        required_monthly_sip_paise=0,
        blended_return_pct=blended_return_pct,
        years=years,
        equity_volatility_pct=Decimal("0"),
        debt_volatility_pct=Decimal("0"),
        target_equity_pct=Decimal("100"),
        target_debt_pct=Decimal("0"),
        rng=np.random.default_rng(42),
    )

    assert just_below.success_probability_no_contribution_pct == 100.0
    assert just_above.success_probability_no_contribution_pct == 0.0


def test_zero_volatility_percentiles_collapse_to_a_single_value() -> None:
    result = run_monte_carlo(
        goal_id="g1",
        current_corpus_paise=10_000_000,
        inflated_target_paise=20_000_000,
        required_monthly_sip_paise=50_000,
        blended_return_pct=Decimal("10"),
        years=5.0,
        equity_volatility_pct=Decimal("0"),
        debt_volatility_pct=Decimal("0"),
        target_equity_pct=Decimal("100"),
        target_debt_pct=Decimal("0"),
        rng=np.random.default_rng(42),
    )

    assert result.percentile_10_corpus_paise == result.percentile_50_corpus_paise
    assert result.percentile_50_corpus_paise == result.percentile_90_corpus_paise


def test_zero_volatility_with_sip_matches_independent_monthly_simulation() -> None:
    current_corpus_paise = 10_000_000
    required_sip_paise = 50_000
    blended_return_pct = Decimal("10")
    years = 5.0
    n_months = 60

    result = run_monte_carlo(
        goal_id="g1",
        current_corpus_paise=current_corpus_paise,
        inflated_target_paise=1,
        required_monthly_sip_paise=required_sip_paise,
        blended_return_pct=blended_return_pct,
        years=years,
        equity_volatility_pct=Decimal("0"),
        debt_volatility_pct=Decimal("0"),
        target_equity_pct=Decimal("100"),
        target_debt_pct=Decimal("0"),
        rng=np.random.default_rng(1),
    )

    # Independent month-by-month simulation (plain Python, not calling into
    # the module under test), using the same geometric monthly-equivalent
    # rate the module documents using.
    monthly_rate = (1.10) ** (1 / 12) - 1
    corpus = float(current_corpus_paise)
    for _ in range(n_months):
        corpus = corpus * (1 + monthly_rate) + required_sip_paise

    assert abs(result.percentile_50_corpus_paise - corpus) < _TOLERANCE_PAISE


def test_seeded_rng_is_deterministic() -> None:
    kwargs = dict(
        goal_id="g1",
        current_corpus_paise=10_000_000,
        inflated_target_paise=15_000_000,
        required_monthly_sip_paise=20_000,
        blended_return_pct=Decimal("10"),
        years=5.0,
        equity_volatility_pct=Decimal("18"),
        debt_volatility_pct=Decimal("3"),
        target_equity_pct=Decimal("70"),
        target_debt_pct=Decimal("30"),
    )

    result_a = run_monte_carlo(rng=np.random.default_rng(7), **kwargs)
    result_b = run_monte_carlo(rng=np.random.default_rng(7), **kwargs)

    assert result_a == result_b


def test_higher_volatility_widens_the_percentile_spread() -> None:
    kwargs = dict(
        goal_id="g1",
        current_corpus_paise=10_000_000,
        inflated_target_paise=15_000_000,
        required_monthly_sip_paise=20_000,
        blended_return_pct=Decimal("10"),
        years=10.0,
        target_equity_pct=Decimal("100"),
        target_debt_pct=Decimal("0"),
    )

    low_vol = run_monte_carlo(
        equity_volatility_pct=Decimal("5"), debt_volatility_pct=Decimal("0"),
        rng=np.random.default_rng(1), **kwargs,
    )
    high_vol = run_monte_carlo(
        equity_volatility_pct=Decimal("30"), debt_volatility_pct=Decimal("0"),
        rng=np.random.default_rng(1), **kwargs,
    )

    low_spread = low_vol.percentile_90_corpus_paise - low_vol.percentile_10_corpus_paise
    high_spread = high_vol.percentile_90_corpus_paise - high_vol.percentile_10_corpus_paise
    assert high_spread > low_spread


def test_easy_goal_has_high_success_probability_and_hard_goal_low() -> None:
    easy = run_monte_carlo(
        goal_id="g1",
        current_corpus_paise=100_000_000,  # already well past target
        inflated_target_paise=10_000_000,
        required_monthly_sip_paise=0,
        blended_return_pct=Decimal("10"),
        years=5.0,
        equity_volatility_pct=Decimal("18"),
        debt_volatility_pct=Decimal("3"),
        target_equity_pct=Decimal("70"),
        target_debt_pct=Decimal("30"),
        rng=np.random.default_rng(3),
    )
    hard = run_monte_carlo(
        goal_id="g1",
        current_corpus_paise=1_000,  # essentially nothing
        inflated_target_paise=1_000_000_000,  # wildly ambitious
        required_monthly_sip_paise=0,
        blended_return_pct=Decimal("10"),
        years=1.0,
        equity_volatility_pct=Decimal("18"),
        debt_volatility_pct=Decimal("3"),
        target_equity_pct=Decimal("70"),
        target_debt_pct=Decimal("30"),
        rng=np.random.default_rng(3),
    )

    assert easy.success_probability_no_contribution_pct > 95.0
    assert hard.success_probability_no_contribution_pct < 5.0
