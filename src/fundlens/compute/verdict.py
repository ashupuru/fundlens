"""Recommendation rubric + verdict engine (PRD FR-6.1, compute-engine.md
§5): transparent and tunable, not a black-box score.

**Scope narrowing from the original 5-component design** (agreed before
building this — see the P1.3 planning discussion): real data supports 4 of
the original 5 components, now scored across 5 named components.

    Component        Original weight   Today
    Rolling alpha     30%               kept (compute/rolling_returns.py, true
                                         monthly-sampled rolling windows as of
                                         P3 — see that module's docstring)
    Consistency       20%               kept (win-rate across the same rolling
                                         windows, pooled over 3yr+5yr only —
                                         1yr is informational-only, never fed
                                         into this rubric, see
                                         rolling_returns.py)
    Cost              20%               split 10/10 across two signals, both
                                         in compute/plan_audit.py: the graded
                                         expense-ratio score, and the binary
                                         Direct-vs-Regular flag. Originally
                                         the flag alone carried all 20 because
                                         no free TER source existed; the Groww
                                         screener scraper has since supplied
                                         one. Split rather than doubled
                                         because a Regular plan simply *is*
                                         ~0.6-1.2pp more TER — the two
                                         measure one underlying thing, and
                                         weighting both fully would
                                         double-count it. They are
                                         complementary in practice: Groww's
                                         feed is direct-plan-only, so TER is
                                         absent for precisely the Regular
                                         holdings the flag already scores 0.
    Overlap/redundancy 15%              omitted entirely — needs monthly
                                         AMC portfolio disclosures, a data
                                         source this app doesn't integrate
    Category fit       15%              kept (compute/allocation.py, needs
                                         P1.4's drift data)

Weights among the kept components live in the assumption registry
(`assumptions/service.py::RubricWeights`), not code, per PRD §5.7 — and are
**renormalized per-verdict** across whichever components are actually
computable for that specific fund (e.g. a fund held <3yr has both rolling
components unavailable and gets a confidence-capped verdict from the
remaining 2, never a score silently computed as if the missing components
were zero).

**Verdict space is HOLD/SELL only, not HOLD/SELL/SWITCH**: a SWITCH verdict
needs a specific alternative fund to switch to, which requires the
Alternatives Engine (FR-9.1, compute-engine.md §8) — not built. Naming a
"switch to X" recommendation without an actual candidate fund would be
fabricating one.

**Tax-friction statement is schema-enforced on SELL** (FR-6.2): `tax_impact`
is always `None` for HOLD and always a *computable* `TaxFrictionStatement`
for SELL — if the rubric's score alone would indicate SELL but
`tax_friction.py` reports `computable=False` (e.g. a debt fund with no
configured marginal tax rate), the verdict is downgraded to HOLD with
`blocked_reason` set, rather than emitting a SELL whose real-world cost
can't be quantified.
"""

import enum
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from fundlens.allocation.service import get_risk_profile
from fundlens.assumptions.service import get_current_rubric_weights
from fundlens.compute.allocation import (
    AllocationDrift,
    compute_allocation_drift,
    compute_category_fit_score,
)
from fundlens.compute.holdings import get_current_holdings
from fundlens.compute.plan_audit import (
    PlanType,
    compute_cost_score,
    detect_plan_type,
    get_category_expense_medians,
)
from fundlens.compute.rolling_returns import (
    RollingReturnSummary,
    compute_rolling_returns,
    summarize_for_rubric,
)
from fundlens.db.models import GrowwFundMetadata, SchemeMetadata
from fundlens.tax.friction import TaxFrictionStatement, compute_tax_friction

OMITTED_COMPONENTS = ("overlap_redundancy",)


class VerdictType(enum.StrEnum):
    HOLD = "hold"
    SELL = "sell"


@dataclass(frozen=True)
class RubricComponent:
    name: str
    score: float | None  # 0-100, higher is better
    weight: float
    reason: str | None  # set when score is None


@dataclass(frozen=True)
class Verdict:
    folio: str
    scheme_key: str
    scheme_name: str
    verdict_type: VerdictType
    overall_score: float | None
    confidence_pct: float
    components: list[RubricComponent]
    reasoning: str
    counterfactual: str | None
    tax_impact: TaxFrictionStatement | None  # non-None only for SELL, and always .computable there
    blocked_reason: str | None
    omitted_components: tuple[str, ...]


_DIRECT_REGULAR_SCORE = {
    PlanType.DIRECT: 100.0,
    PlanType.REGULAR: 0.0,
    PlanType.UNKNOWN: None,
}


def _weighted_average(components: list[RubricComponent]) -> float | None:
    usable = [c for c in components if c.score is not None]
    total_weight = sum(c.weight for c in usable)
    if total_weight <= 0:
        return None
    return sum(c.score * c.weight for c in usable if c.score is not None) / total_weight


def _build_reasoning(verdict_type: VerdictType, components: list[RubricComponent]) -> str:
    parts = [
        f"{c.name}={c.score:.0f}/100" if c.score is not None else f"{c.name}=n/a({c.reason})"
        for c in components
    ]
    return f"{verdict_type.value.upper()} — component scores: {', '.join(parts)}."


def _build_counterfactual(
    verdict_type: VerdictType, components: list[RubricComponent]
) -> str | None:
    usable = [c for c in components if c.score is not None]
    if not usable:
        return None
    if verdict_type == VerdictType.SELL:
        best = max(usable, key=lambda c: c.score if c.score is not None else 0.0)
        return f"Would move toward HOLD if {best.name} improved further."
    worst = min(usable, key=lambda c: c.score if c.score is not None else 100.0)
    return f"Would move toward SELL if {worst.name} deteriorated further."


def compute_verdict(
    session: Session,
    folio: str,
    scheme_key: str,
    scheme_name: str,
    amfi_code: str | None,
    metadata: SchemeMetadata | None,
    drift: AllocationDrift | None,
    expense_medians: Mapping[str, Decimal],
) -> Verdict:
    """`expense_medians` is required rather than looked up here because it
    is an aggregate over the entire scheme universe — the caller computes it
    once via `plan_audit.get_category_expense_medians` and reuses it across
    holdings, the same way `metadata` and `drift` are already batched. Pass
    an empty mapping to score without peer data; the cost component then
    reports itself unscored rather than guessing.
    """
    weights = get_current_rubric_weights(session)
    components: list[RubricComponent] = []

    rolling: RollingReturnSummary | None = None
    if amfi_code is not None and metadata is not None:
        windows = compute_rolling_returns(
            session,
            amfi_code,
            metadata.category_raw,
            metadata.tax_asset_class,
            window_years=(3, 5),
        )
        rolling = summarize_for_rubric(windows)

    if rolling is not None and rolling.mean_alpha_pct is not None:
        # Alpha is a return-delta in percentage points, not a 0-100 score —
        # map it onto the same 0-100 scale as the other components: 50 =
        # matched the benchmark exactly, +/-1pp of alpha = +/-2 score
        # points, capped at the bounds. A judgment call, not a canonical
        # formula, same as category_fit's scaling.
        alpha_score = max(0.0, min(100.0, 50 + rolling.mean_alpha_pct * 2))
        components.append(RubricComponent("rolling_alpha", alpha_score, weights.alpha, None))
    else:
        components.append(
            RubricComponent("rolling_alpha", None, weights.alpha, "insufficient_history")
        )

    if rolling is not None and rolling.consistency_pct is not None:
        components.append(
            RubricComponent("consistency", rolling.consistency_pct, weights.consistency, None)
        )
    else:
        components.append(
            RubricComponent("consistency", None, weights.consistency, "insufficient_history")
        )

    if metadata is not None and drift is not None:
        fit_score, fit_reason = compute_category_fit_score(metadata.tax_asset_class, drift)
        components.append(
            RubricComponent("category_fit", fit_score, weights.category_fit, fit_reason)
        )
    elif metadata is not None:
        components.append(
            RubricComponent("category_fit", None, weights.category_fit, "no_risk_profile_set")
        )
    else:
        components.append(
            RubricComponent(
                "category_fit", None, weights.category_fit, "scheme_metadata_unavailable"
            )
        )

    plan_type = detect_plan_type(scheme_name)
    direct_regular_score = _DIRECT_REGULAR_SCORE[plan_type]
    direct_regular_reason = None if direct_regular_score is not None else "plan_type_unknown"
    components.append(
        RubricComponent(
            "direct_vs_regular", direct_regular_score, weights.direct_regular, direct_regular_reason
        )
    )

    # Primary-key get, so it hits the identity map rather than the DB when
    # the caller has already loaded the row.
    groww = session.get(GrowwFundMetadata, amfi_code) if amfi_code is not None else None
    cost_score, cost_reason = compute_cost_score(
        groww.expense_ratio if groww is not None else None,
        groww.sub_category if groww is not None else None,
        expense_medians,
        plan_type,
    )
    components.append(
        RubricComponent(
            "cost_expense_ratio", cost_score, weights.cost_expense_ratio, cost_reason
        )
    )

    overall_score = _weighted_average(components)
    available = sum(1 for c in components if c.score is not None)
    confidence_pct = available / len(components) * 100

    tentative_type = (
        VerdictType.SELL
        if overall_score is not None and overall_score < weights.sell_threshold_pct
        else VerdictType.HOLD
    )

    verdict_type = tentative_type
    tax_impact: TaxFrictionStatement | None = None
    blocked_reason: str | None = None
    if tentative_type == VerdictType.SELL:
        tax_impact = compute_tax_friction(session, folio, scheme_key)
        if not tax_impact.computable:
            verdict_type = VerdictType.HOLD
            blocked_reason = (
                f"rubric_indicated_sell_but_tax_impact_not_computable:{tax_impact.reason}"
            )
            tax_impact = None

    return Verdict(
        folio=folio,
        scheme_key=scheme_key,
        scheme_name=scheme_name,
        verdict_type=verdict_type,
        overall_score=overall_score,
        confidence_pct=confidence_pct,
        components=components,
        reasoning=_build_reasoning(verdict_type, components),
        counterfactual=_build_counterfactual(verdict_type, components),
        tax_impact=tax_impact,
        blocked_reason=blocked_reason,
        omitted_components=OMITTED_COMPONENTS,
    )


def get_all_verdicts(session: Session) -> list[Verdict]:
    holdings = get_current_holdings(session)
    if not holdings:
        return []

    profile = get_risk_profile(session)
    drift = compute_allocation_drift(session, profile) if profile is not None else None

    amfi_codes = {h.amfi_code for h in holdings if h.amfi_code}
    metadata_by_code = {
        m.amfi_code: m
        for m in session.scalars(
            select(SchemeMetadata).where(SchemeMetadata.amfi_code.in_(amfi_codes))
        )
    }
    expense_medians = get_category_expense_medians(session)

    return [
        compute_verdict(
            session,
            folio=h.folio,
            scheme_key=h.scheme_key,
            scheme_name=h.scheme_name,
            amfi_code=h.amfi_code,
            metadata=metadata_by_code.get(h.amfi_code) if h.amfi_code else None,
            drift=drift,
            expense_medians=expense_medians,
        )
        for h in holdings
    ]
