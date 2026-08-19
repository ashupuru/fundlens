"""Per-fund analytics tools.

The portfolio snapshot already carries a rollup of every holding, so these
exist for the other axis: drilling into *one* fund over a window the
question chooses. `compute_rolling_returns` and `compute_risk_metrics` in
particular are parameterised by window length, which a fixed snapshot
cannot enumerate.

`list_holdings` is the discovery tool -- the model needs a real amfi_code
before it can call anything else here, and guessing one would produce a
confident answer about a fund the user doesn't own.
"""

from typing import Any

from sqlalchemy.orm import Session

from fundlens.ai.tools.registry import Tool, ToolError, object_schema
from fundlens.ai.tools.serialize import compact, money, number
from fundlens.assumptions.service import get_current_risk_metrics_assumptions
from fundlens.compute.holdings import get_current_holdings
from fundlens.compute.metrics import compute_fund_metrics
from fundlens.compute.plan_audit import get_category_expense_medians
from fundlens.compute.risk_metrics import compute_risk_adjusted_metrics
from fundlens.compute.rolling_returns import compute_rolling_returns
from fundlens.compute.verdict import compute_verdict
from fundlens.db.models import GrowwFundMetadata, SchemeMetadata

_AMFI_CODE = {
    "type": "string",
    "description": "AMFI scheme code of a held fund, from list_holdings",
}
_WINDOW_YEARS = {
    "type": "array",
    "items": {"type": "integer"},
    "description": "Rolling window lengths in years, e.g. [3, 5]",
}


def _require_scheme_metadata(session: Session, amfi_code: str) -> SchemeMetadata:
    metadata = session.get(SchemeMetadata, amfi_code)
    if metadata is None:
        raise ToolError(
            f"No scheme metadata for AMFI code {amfi_code!r} — category and tax asset class "
            "are unknown, so performance cannot be benchmarked."
        )
    return metadata


def _list_holdings(session: Session) -> dict[str, Any]:
    holdings = get_current_holdings(session)
    return {
        "holdings": [
            compact(
                {
                    "scheme_name": h.scheme_name,
                    "amfi_code": h.amfi_code,
                    "amc": h.amc,
                    "units": number(h.units_remaining, 4),
                    "cost_basis": money(h.cost_basis_paise),
                }
            )
            for h in holdings
        ],
        "count": len(holdings),
    }


def _fund_metrics(session: Session, amfi_code: str) -> dict[str, Any]:
    metrics = compute_fund_metrics(session, amfi_code)
    if metrics is None:
        raise ToolError(f"No holding found for AMFI code {amfi_code!r}")
    return compact(
        {
            "amfi_code": amfi_code,
            "xirr_pct": number(metrics.xirr),
            "xirr_reason": metrics.xirr_reason,
            "invested": money(metrics.invested_paise),
            "received": money(metrics.received_paise),
            "current_value": money(metrics.current_value_paise),
            "positions_missing_nav": metrics.positions_missing_nav,
        }
    )


def _rolling_returns(
    session: Session, amfi_code: str, window_years: list[int] | None = None
) -> dict[str, Any]:
    metadata = _require_scheme_metadata(session, amfi_code)
    windows = compute_rolling_returns(
        session,
        amfi_code,
        metadata.category_raw,
        metadata.tax_asset_class,
        window_years=tuple(window_years or [3, 5]),
    )
    return {
        "amfi_code": amfi_code,
        "windows": [
            compact(
                {
                    "years": w.years,
                    "sample_count": len(w.samples),
                    "mean_alpha_pct": number(w.mean_alpha_pct),
                    "consistency_pct": number(w.consistency_pct),
                    "reason": w.reason,
                }
            )
            for w in windows
        ],
    }


def _risk_metrics(
    session: Session, amfi_code: str, window_years: list[int] | None = None
) -> dict[str, Any]:
    metadata = _require_scheme_metadata(session, amfi_code)
    risk_free = get_current_risk_metrics_assumptions(session).risk_free_rate_pct
    kwargs: dict[str, Any] = {}
    if window_years:
        kwargs["window_years"] = tuple(window_years)
    windows = compute_risk_adjusted_metrics(
        session, amfi_code, metadata.category_raw, metadata.tax_asset_class, risk_free, **kwargs
    )

    def stats(s: Any) -> dict[str, Any] | None:
        if s is None:
            return None
        return compact(
            {
                "sharpe": number(s.sharpe),
                "sortino": number(s.sortino),
                "max_drawdown_pct": number(s.max_drawdown_pct),
            }
        )

    return {
        "amfi_code": amfi_code,
        "risk_free_rate_pct": number(risk_free),
        "windows": [
            compact(
                {
                    "years": w.years,
                    "fund": stats(w.fund),
                    "benchmark": stats(w.benchmark),
                    "reason": w.reason,
                }
            )
            for w in windows
        ],
    }


def _fund_verdict(session: Session, amfi_code: str) -> dict[str, Any]:
    holding = next(
        (h for h in get_current_holdings(session) if h.amfi_code == amfi_code), None
    )
    if holding is None:
        raise ToolError(f"No holding found for AMFI code {amfi_code!r}")

    verdict = compute_verdict(
        session,
        folio=holding.folio,
        scheme_key=holding.scheme_key,
        scheme_name=holding.scheme_name,
        amfi_code=amfi_code,
        metadata=session.get(SchemeMetadata, amfi_code),
        drift=None,
        expense_medians=get_category_expense_medians(session),
    )
    return compact(
        {
            "scheme_name": verdict.scheme_name,
            "verdict": verdict.verdict_type.value,
            "overall_score": number(verdict.overall_score),
            "confidence_pct": number(verdict.confidence_pct),
            "components": [
                compact({"name": c.name, "score": number(c.score), "reason": c.reason})
                for c in verdict.components
            ],
            "omitted_components": list(verdict.omitted_components),
            "blocked_reason": verdict.blocked_reason,
        }
    )


def _fund_costs(session: Session, amfi_code: str) -> dict[str, Any]:
    groww = session.get(GrowwFundMetadata, amfi_code)
    if groww is None:
        raise ToolError(
            f"No fund metadata for AMFI code {amfi_code!r} — the screener feed is "
            "direct-plan-only, so regular plans are absent from it."
        )
    medians = get_category_expense_medians(session)
    return compact(
        {
            "scheme_name": groww.scheme_name,
            "category": groww.category,
            "sub_category": groww.sub_category,
            "expense_ratio_pct": number(groww.expense_ratio, 4),
            "category_median_expense_ratio_pct": number(
                medians.get(groww.sub_category or ""), 4
            ),
            "aum_crore": number(groww.aum),
            "groww_rating": groww.groww_rating,
        }
    )


FUND_TOOLS = [
    Tool(
        name="list_holdings",
        description=(
            "Every currently-held fund with its AMFI code, units and cost basis. Call this "
            "first to find the amfi_code the other fund tools need — never guess one."
        ),
        input_schema=object_schema({}),
        fn=_list_holdings,
    ),
    Tool(
        name="compute_fund_metrics",
        description=(
            "Position-level performance for one held fund: XIRR, amount invested, amount "
            "received from redemptions, and current value."
        ),
        input_schema=object_schema({"amfi_code": _AMFI_CODE}, required=["amfi_code"]),
        fn=_fund_metrics,
    ),
    Tool(
        name="compute_rolling_returns",
        description=(
            "Rolling-window performance of the scheme against its benchmark: mean alpha and "
            "the share of windows it beat the benchmark, per window length. Scheme-level "
            "performance, not this investor's entry timing."
        ),
        input_schema=object_schema(
            {"amfi_code": _AMFI_CODE, "window_years": _WINDOW_YEARS}, required=["amfi_code"]
        ),
        fn=_rolling_returns,
    ),
    Tool(
        name="compute_risk_metrics",
        description=(
            "Risk-adjusted metrics for one fund and its benchmark: Sharpe, Sortino and "
            "maximum drawdown, per window length."
        ),
        input_schema=object_schema(
            {"amfi_code": _AMFI_CODE, "window_years": _WINDOW_YEARS}, required=["amfi_code"]
        ),
        fn=_risk_metrics,
    ),
    Tool(
        name="get_fund_verdict",
        description=(
            "The rubric's HOLD/SELL verdict for one held fund, with every component score, "
            "the confidence, and any component that could not be scored and why."
        ),
        input_schema=object_schema({"amfi_code": _AMFI_CODE}, required=["amfi_code"]),
        fn=_fund_verdict,
    ),
    Tool(
        name="get_fund_costs",
        description=(
            "Expense ratio for one fund alongside the median for its category, plus AUM, "
            "category and rating."
        ),
        input_schema=object_schema({"amfi_code": _AMFI_CODE}, required=["amfi_code"]),
        fn=_fund_costs,
    ),
]
