"""PRD §5.7 assumption registry: tax thresholds and the LTCG exemption
limit as versioned, editable config (db/models.py AssumptionVersion,
Assumption) rather than code constants — needed now because the P1.2 tax
engine is the first consumer (dev plan P1.2).

Only the values the current tax engine actually reads are seeded/typed
here (`TaxAssumptions`); inflation % and expected-return assumptions named
in the PRD belong to the goal-planning phase (P2+) and aren't added until
something reads them — an untyped, unread assumption key is dead config.

Goal planning (P2.4, `compute/goal.py`) is now a reader too: see
`GoalAssumptions`/`get_current_goal_assumptions` below.
"""

import enum
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from fundlens.db.models import Assumption, AssumptionVersion


class AssumptionKey(enum.StrEnum):
    EQUITY_LTCG_THRESHOLD_DAYS = "equity_ltcg_threshold_days"
    DEBT_LTCG_THRESHOLD_DAYS = "debt_ltcg_threshold_days"
    DEBT_SPECIFIED_FUND_CUTOFF = "debt_specified_fund_cutoff"
    EQUITY_GRANDFATHER_CUTOFF = "equity_grandfather_cutoff"
    EQUITY_LTCG_EXEMPTION_PAISE_PER_FY = "equity_ltcg_exemption_paise_per_fy"
    EQUITY_STCG_RATE_PCT = "equity_stcg_rate_pct"
    EQUITY_LTCG_RATE_PCT = "equity_ltcg_rate_pct"
    # Deliberately NOT in DEFAULT_ASSUMPTIONS below: debt gains are taxed at
    # the investor's marginal income-tax slab, which this app has no way to
    # know — defaulting it to any number (even "the top slab, conservatively")
    # would be presenting a guess as the user's real tax rate. Must be set
    # explicitly via POST /assumptions before any debt-fund tax-friction
    # statement (compute-engine.md §3.5) can be computed; until then it's
    # reported as unconfigured, not silently assumed.
    DEBT_MARGINAL_TAX_RATE_PCT = "debt_marginal_tax_rate_pct"
    # Recommendation rubric (compute-engine.md §5) weights, out of the
    # components P1.3 actually has real data for — overlap/redundancy
    # (FR-5.3) and expense-ratio cost have no data source (P1.3 scope
    # decision) and are omitted rather than weighted-and-faked. Default
    # values keep the PRD table's original relative proportions among the
    # 4 remaining components (alpha 30, consistency 20, category_fit 15,
    # cost 20 -> direct_regular inherits cost's weight).
    RUBRIC_WEIGHT_ALPHA = "rubric_weight_alpha"
    RUBRIC_WEIGHT_CONSISTENCY = "rubric_weight_consistency"
    RUBRIC_WEIGHT_CATEGORY_FIT = "rubric_weight_category_fit"
    RUBRIC_WEIGHT_DIRECT_REGULAR = "rubric_weight_direct_regular"
    RUBRIC_SELL_THRESHOLD_PCT = "rubric_sell_threshold_pct"
    # Goal planning (compute-engine.md §10): expected returns are blended
    # per-goal from the existing RiskProfile's target_equity_pct/
    # target_debt_pct rather than a separate per-persona table, so only the
    # two per-asset-class rates need to live here.
    INFLATION_PCT = "inflation_pct"
    EXPECTED_EQUITY_RETURN_PCT = "expected_equity_return_pct"
    EXPECTED_DEBT_RETURN_PCT = "expected_debt_return_pct"
    # Risk-adjusted metrics (compute-engine.md §12, FR-3.4): Sharpe ratio's
    # excess-return numerator.
    RISK_FREE_RATE_PCT = "risk_free_rate_pct"
    # Monte Carlo goal simulation (compute-engine.md §14, FR-8.3): annualized
    # return volatility (stdev) per asset class, blended the same simple
    # weighted-average way expected returns already are.
    EQUITY_VOLATILITY_PCT = "equity_volatility_pct"
    DEBT_VOLATILITY_PCT = "debt_volatility_pct"


DEFAULT_ASSUMPTIONS: dict[AssumptionKey, str] = {
    AssumptionKey.EQUITY_LTCG_THRESHOLD_DAYS: "365",
    AssumptionKey.DEBT_LTCG_THRESHOLD_DAYS: "1095",
    AssumptionKey.DEBT_SPECIFIED_FUND_CUTOFF: "2023-04-01",
    # Section 112A 4th/5th proviso valuation date — NOT when the rule
    # started applying (that's 2018-04-01/FY2018-19); this is the specific
    # date whose NAV grandfathers pre-existing equity cost basis.
    AssumptionKey.EQUITY_GRANDFATHER_CUTOFF: "2018-01-31",
    AssumptionKey.EQUITY_LTCG_EXEMPTION_PAISE_PER_FY: "12500000",  # Rs 1,25,000
    # Current law post-Budget-2024 (rates effective 23-Jul-2024) — fixed,
    # publicly documented figures, unlike the debt slab rate below.
    AssumptionKey.EQUITY_STCG_RATE_PCT: "20",
    AssumptionKey.EQUITY_LTCG_RATE_PCT: "12.5",
    AssumptionKey.RUBRIC_WEIGHT_ALPHA: "30",
    AssumptionKey.RUBRIC_WEIGHT_CONSISTENCY: "20",
    AssumptionKey.RUBRIC_WEIGHT_CATEGORY_FIT: "15",
    AssumptionKey.RUBRIC_WEIGHT_DIRECT_REGULAR: "20",
    AssumptionKey.RUBRIC_SELL_THRESHOLD_PCT: "35",
    # Reasonable Indian-market long-term planning defaults, editable via
    # POST /assumptions like everything else here — not asserted as a
    # forecast.
    AssumptionKey.INFLATION_PCT: "6",
    AssumptionKey.EXPECTED_EQUITY_RETURN_PCT: "12",
    AssumptionKey.EXPECTED_DEBT_RETURN_PCT: "7",
    # Roughly a 10yr G-Sec / bank FD rate — the conventional Sharpe-ratio
    # "risk-free" proxy for Indian markets. Editable, not asserted as
    # canonical.
    AssumptionKey.RISK_FREE_RATE_PCT: "7",
    # Reasonable Indian-market annualized-volatility defaults (broad equity
    # index / debt fund NAV stdev order of magnitude), editable — not
    # asserted as a forecast.
    AssumptionKey.EQUITY_VOLATILITY_PCT: "18",
    AssumptionKey.DEBT_VOLATILITY_PCT: "3",
}


def _seed_default_version(session: Session) -> AssumptionVersion:
    version = AssumptionVersion(label="Default", is_current=True)
    session.add(version)
    session.flush()
    for key, value in DEFAULT_ASSUMPTIONS.items():
        session.add(Assumption(version_id=version.id, key=key.value, value=value))
    session.commit()
    return version


def get_current_version(session: Session) -> AssumptionVersion:
    version = session.scalars(
        select(AssumptionVersion).where(AssumptionVersion.is_current.is_(True))
    ).first()
    return version if version is not None else _seed_default_version(session)


def get_values(session: Session, version_id: str) -> dict[str, str]:
    rows = session.scalars(select(Assumption).where(Assumption.version_id == version_id)).all()
    return {r.key: r.value for r in rows}


def get_current_values(session: Session) -> dict[str, str]:
    """Current-version values, with any AssumptionKey added to
    DEFAULT_ASSUMPTIONS *after* this DB's version was first seeded
    backfilled transparently (never persisted, never overriding an
    explicitly-stored value) -- otherwise every new key silently
    KeyErrors on any pre-existing database until the user happens to
    POST /assumptions a new version. Deliberately NOT applied in
    get_values() for an arbitrary (e.g. historical) version_id: a past
    version legitimately not having a key that didn't exist yet is
    correct, and backfilling there would misrepresent what that version
    actually pinned, breaking the reproducibility guarantee versions
    exist for.
    """
    defaults = {k.value: v for k, v in DEFAULT_ASSUMPTIONS.items()}
    stored = get_values(session, get_current_version(session).id)
    return {**defaults, **stored}


def list_versions(session: Session) -> list[AssumptionVersion]:
    return list(
        session.scalars(select(AssumptionVersion).order_by(AssumptionVersion.created_at.desc()))
    )


def create_version(session: Session, label: str, overrides: dict[str, str]) -> AssumptionVersion:
    unknown = set(overrides) - {k.value for k in AssumptionKey}
    if unknown:
        raise ValueError(f"Unknown assumption key(s): {sorted(unknown)}")

    current_values = get_current_values(session)
    new_values = {**current_values, **overrides}

    session.execute(
        update(AssumptionVersion).where(AssumptionVersion.is_current.is_(True)).values(
            is_current=False
        )
    )
    new_version = AssumptionVersion(label=label, is_current=True)
    session.add(new_version)
    session.flush()
    for key, value in new_values.items():
        session.add(Assumption(version_id=new_version.id, key=key, value=value))
    session.commit()
    return new_version


@dataclass(frozen=True)
class TaxAssumptions:
    equity_ltcg_threshold_days: int
    debt_ltcg_threshold_days: int
    debt_specified_fund_cutoff: date
    equity_grandfather_cutoff: date
    equity_ltcg_exemption_paise_per_fy: int


def get_current_tax_assumptions(session: Session) -> TaxAssumptions:
    values = get_current_values(session)
    return TaxAssumptions(
        equity_ltcg_threshold_days=int(values[AssumptionKey.EQUITY_LTCG_THRESHOLD_DAYS.value]),
        debt_ltcg_threshold_days=int(values[AssumptionKey.DEBT_LTCG_THRESHOLD_DAYS.value]),
        debt_specified_fund_cutoff=date.fromisoformat(
            values[AssumptionKey.DEBT_SPECIFIED_FUND_CUTOFF.value]
        ),
        equity_grandfather_cutoff=date.fromisoformat(
            values[AssumptionKey.EQUITY_GRANDFATHER_CUTOFF.value]
        ),
        equity_ltcg_exemption_paise_per_fy=int(
            values[AssumptionKey.EQUITY_LTCG_EXEMPTION_PAISE_PER_FY.value]
        ),
    )


@dataclass(frozen=True)
class RubricTaxRates:
    equity_stcg_rate_pct: Decimal
    equity_ltcg_rate_pct: Decimal
    debt_marginal_tax_rate_pct: Decimal | None  # None => not configured, never guessed


def get_current_rubric_tax_rates(session: Session) -> RubricTaxRates:
    values = get_current_values(session)
    debt_rate_raw = values.get(AssumptionKey.DEBT_MARGINAL_TAX_RATE_PCT.value)
    return RubricTaxRates(
        equity_stcg_rate_pct=Decimal(values[AssumptionKey.EQUITY_STCG_RATE_PCT.value]),
        equity_ltcg_rate_pct=Decimal(values[AssumptionKey.EQUITY_LTCG_RATE_PCT.value]),
        debt_marginal_tax_rate_pct=Decimal(debt_rate_raw) if debt_rate_raw is not None else None,
    )


@dataclass(frozen=True)
class RubricWeights:
    alpha: float
    consistency: float
    category_fit: float
    direct_regular: float
    sell_threshold_pct: float


def get_current_rubric_weights(session: Session) -> RubricWeights:
    values = get_current_values(session)
    return RubricWeights(
        alpha=float(values[AssumptionKey.RUBRIC_WEIGHT_ALPHA.value]),
        consistency=float(values[AssumptionKey.RUBRIC_WEIGHT_CONSISTENCY.value]),
        category_fit=float(values[AssumptionKey.RUBRIC_WEIGHT_CATEGORY_FIT.value]),
        direct_regular=float(values[AssumptionKey.RUBRIC_WEIGHT_DIRECT_REGULAR.value]),
        sell_threshold_pct=float(values[AssumptionKey.RUBRIC_SELL_THRESHOLD_PCT.value]),
    )


@dataclass(frozen=True)
class GoalAssumptions:
    inflation_pct: Decimal
    expected_equity_return_pct: Decimal
    expected_debt_return_pct: Decimal


def get_current_goal_assumptions(session: Session) -> GoalAssumptions:
    values = get_current_values(session)
    return GoalAssumptions(
        inflation_pct=Decimal(values[AssumptionKey.INFLATION_PCT.value]),
        expected_equity_return_pct=Decimal(
            values[AssumptionKey.EXPECTED_EQUITY_RETURN_PCT.value]
        ),
        expected_debt_return_pct=Decimal(values[AssumptionKey.EXPECTED_DEBT_RETURN_PCT.value]),
    )


@dataclass(frozen=True)
class RiskMetricsAssumptions:
    risk_free_rate_pct: Decimal


def get_current_risk_metrics_assumptions(session: Session) -> RiskMetricsAssumptions:
    values = get_current_values(session)
    return RiskMetricsAssumptions(
        risk_free_rate_pct=Decimal(values[AssumptionKey.RISK_FREE_RATE_PCT.value])
    )


@dataclass(frozen=True)
class MonteCarloAssumptions:
    equity_volatility_pct: Decimal
    debt_volatility_pct: Decimal


def get_current_monte_carlo_assumptions(session: Session) -> MonteCarloAssumptions:
    values = get_current_values(session)
    return MonteCarloAssumptions(
        equity_volatility_pct=Decimal(values[AssumptionKey.EQUITY_VOLATILITY_PCT.value]),
        debt_volatility_pct=Decimal(values[AssumptionKey.DEBT_VOLATILITY_PCT.value]),
    )
