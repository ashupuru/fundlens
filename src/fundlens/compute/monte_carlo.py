"""Monte Carlo goal-probability simulation (PRD FR-8.3, phase P3,
compute-engine.md §14): a probability lens on top of Goal Planning's
(P2.4, `compute/goal.py`) deterministic projection, which assumes returns
exactly equal the blended expected rate every year -- this simulates
thousands of random monthly-return paths instead, reporting the fraction
that actually reach the (inflation-adjusted) target.

Two scenarios, directly extending Goal Planning's two headline numbers
rather than duplicating just one: "no further contribution" (a
probability lens on `projected_corpus_paise`) and "with the required SIP"
(shows how the deterministic required-SIP calculation's "exactly the
expected return every year" assumption holds up under real volatility).

Volatility is blended per asset class the same simple weighted-average way
`compute/goal.py`'s expected return already is -- not a covariance-based
portfolio-variance formula, which would need an equity-debt correlation
assumption this app doesn't have and isn't worth introducing for a
single-portfolio, two-asset-class model. Documented simplification, not
asserted as canonical.

Money is float (paise-denominated) throughout this module, not Decimal --
matching this codebase's existing convention for market-derived
statistical quantities (XIRR, rolling-return CAGR, Sharpe/Sortino are all
float) as opposed to Decimal, which is reserved for exact accounting of
real transaction amounts. A Monte Carlo simulation is inherently
statistical; Decimal precision would be a false sense of accuracy here.
"""

from dataclasses import dataclass
from decimal import Decimal

import numpy as np

_SIMULATIONS = 5000
_MONTHS_PER_YEAR = 12


@dataclass(frozen=True)
class MonteCarloResult:
    goal_id: str
    simulations_run: int
    success_probability_no_contribution_pct: float
    success_probability_with_required_sip_pct: float
    percentile_10_corpus_paise: int
    percentile_50_corpus_paise: int
    percentile_90_corpus_paise: int


def _simulate_paths(
    rng: np.random.Generator,
    n_months: int,
    starting_corpus_paise: float,
    monthly_contribution_paise: float,
    monthly_mean: float,
    monthly_stdev: float,
) -> np.ndarray:
    """Simulates _SIMULATIONS independent monthly-return paths, compounding
    a starting lump sum plus a fixed monthly contribution added at the end
    of each period (ordinary-annuity convention, matching
    compute/goal.py's _required_monthly_sip_paise derivation exactly --
    verified to collapse to the identical closed-form result when
    monthly_stdev is 0, see tests/compute/test_monte_carlo.py). Returns
    the final corpus (paise) for every simulated path."""
    returns = rng.normal(loc=monthly_mean, scale=monthly_stdev, size=(_SIMULATIONS, n_months))
    corpus = np.full(_SIMULATIONS, starting_corpus_paise)
    for month in range(n_months):
        corpus = corpus * (1 + returns[:, month]) + monthly_contribution_paise
    return corpus


def run_monte_carlo(
    goal_id: str,
    current_corpus_paise: int,
    inflated_target_paise: int,
    required_monthly_sip_paise: int,
    blended_return_pct: Decimal,
    years: float,
    equity_volatility_pct: Decimal,
    debt_volatility_pct: Decimal,
    target_equity_pct: Decimal,
    target_debt_pct: Decimal,
    rng: np.random.Generator | None = None,
) -> MonteCarloResult:
    if rng is None:
        rng = np.random.default_rng()

    n_months = max(1, round(years * _MONTHS_PER_YEAR))
    blended_volatility_pct = (
        target_equity_pct / 100 * equity_volatility_pct
        + target_debt_pct / 100 * debt_volatility_pct
    )
    # Geometric monthly-equivalent rate (not annual/12, a common but biased
    # simplification): 12 months of compounding at this rate reproduces
    # *exactly* the stated annual rate, so the zero-volatility case
    # collapses to precisely the same annual compounding
    # compute/goal.py's deterministic formula uses.
    annual_growth = 1 + float(blended_return_pct) / 100
    monthly_mean = annual_growth ** (1 / _MONTHS_PER_YEAR) - 1
    monthly_stdev = float(blended_volatility_pct) / 100 / (_MONTHS_PER_YEAR**0.5)

    no_contribution = _simulate_paths(
        rng, n_months, float(current_corpus_paise), 0.0, monthly_mean, monthly_stdev
    )
    with_sip = _simulate_paths(
        rng,
        n_months,
        float(current_corpus_paise),
        float(required_monthly_sip_paise),
        monthly_mean,
        monthly_stdev,
    )

    target = float(inflated_target_paise)
    success_no_contribution = float(np.mean(no_contribution >= target)) * 100
    success_with_sip = float(np.mean(with_sip >= target)) * 100
    p10, p50, p90 = np.percentile(with_sip, [10, 50, 90])

    return MonteCarloResult(
        goal_id=goal_id,
        simulations_run=_SIMULATIONS,
        success_probability_no_contribution_pct=success_no_contribution,
        success_probability_with_required_sip_pct=success_with_sip,
        percentile_10_corpus_paise=round(p10),
        percentile_50_corpus_paise=round(p50),
        percentile_90_corpus_paise=round(p90),
    )


@dataclass(frozen=True)
class RetirementMonteCarloResult:
    simulations_run: int
    success_probability_pct: float  # corpus still positive at life expectancy
    median_depletion_age: int | None  # among failing paths only; None if none fail
    percentile_10_terminal_corpus_paise: int
    percentile_50_terminal_corpus_paise: int
    percentile_90_terminal_corpus_paise: int


def run_retirement_monte_carlo(
    *,
    starting_corpus_paise: int,
    annual_investment_paise: int,
    step_up_pct: Decimal,
    current_age: int,
    retirement_age: int,
    life_expectancy: int,
    first_year_withdrawal_paise: int,
    lump_sum_by_age: dict[int, int],
    pre_return_pct: Decimal,
    post_return_pct: Decimal,
    volatility_pct: Decimal,
    inflation_pct: Decimal,
    rng: np.random.Generator | None = None,
) -> RetirementMonteCarloResult:
    """Sequence-of-returns risk on the retirement corpus (spec §26).

    **Why this isn't redundant with the deterministic projection.** That
    projection earns the *average* return every single year. Real markets
    deliver the same average in an order, and order matters enormously once
    you're withdrawing: a poor first decade of retirement sells units at
    depressed prices, permanently shrinking the base the later good years
    compound on. Two paths with identical mean returns can therefore end
    solvent or bankrupt. Averaging hides exactly the risk a retiree cares
    about, which is why a probability belongs next to the point estimate.

    **Annual steps, deliberately** -- `compute/retirement.py` steps annually,
    so this does too. Monthly steps would be finer-grained but could no
    longer collapse *exactly* onto the deterministic result at zero
    volatility, and that exact collapse is this module's correctness gate
    (tests/compute/test_retirement_monte_carlo.py): with volatility 0 every
    simulated path must reproduce the deterministic engine's own terminal
    corpus and depletion age. The year ordering below mirrors that engine
    line for line -- return on the opening balance, then contribution or
    withdrawal, then lump sums, then the floor at zero.

    **Returns are stochastic; inflation is not.** Sequence-of-returns is the
    dominant and best-evidenced retirement risk, and equity/debt volatility
    are already established assumptions in this app. Inflation variability
    (spec §26 also lists it) would need a volatility figure this app has no
    grounded basis for, so it is deferred rather than guessed -- the same
    treatment `debt_marginal_tax_rate_pct` gets in the assumption registry.

    A corpus at zero is absorbing: returns on zero are zero and further
    withdrawals clamp back to zero, so a path that depletes stays depleted
    rather than mathematically recovering.
    """
    if rng is None:
        rng = np.random.default_rng()

    accumulation_years = retirement_age - current_age
    retirement_years = life_expectancy - retirement_age
    total_years = accumulation_years + retirement_years

    sigma = float(volatility_pct) / 100
    pre_mu = float(pre_return_pct) / 100
    post_mu = float(post_return_pct) / 100

    # One draw per path per year; the accumulation and retirement phases use
    # different means (allocation typically de-risks at retirement) but the
    # same volatility.
    means = np.array([pre_mu] * accumulation_years + [post_mu] * retirement_years)
    returns = rng.normal(loc=means, scale=sigma, size=(_SIMULATIONS, total_years))

    corpus = np.full(_SIMULATIONS, float(starting_corpus_paise))
    depleted_at = np.full(_SIMULATIONS, -1, dtype=int)  # -1 => never depleted

    investment = float(annual_investment_paise)
    for i in range(accumulation_years):
        age = current_age + i
        corpus = corpus * (1 + returns[:, i]) + investment
        corpus = np.maximum(corpus - lump_sum_by_age.get(age, 0), 0.0)
        investment *= 1 + float(step_up_pct) / 100

    withdrawal = float(first_year_withdrawal_paise)
    for i in range(retirement_years):
        age = retirement_age + i
        corpus = corpus * (1 + returns[:, accumulation_years + i]) - withdrawal
        corpus = corpus - lump_sum_by_age.get(age, 0)
        newly_depleted = (corpus <= 0) & (depleted_at < 0)
        depleted_at = np.where(newly_depleted, age, depleted_at)
        corpus = np.maximum(corpus, 0.0)
        withdrawal *= 1 + float(inflation_pct) / 100

    survived = depleted_at < 0
    failures = depleted_at[~survived]
    p10, p50, p90 = np.percentile(corpus, [10, 50, 90])

    return RetirementMonteCarloResult(
        simulations_run=_SIMULATIONS,
        success_probability_pct=float(np.mean(survived)) * 100,
        median_depletion_age=int(np.median(failures)) if failures.size else None,
        percentile_10_terminal_corpus_paise=round(p10),
        percentile_50_terminal_corpus_paise=round(p50),
        percentile_90_terminal_corpus_paise=round(p90),
    )
