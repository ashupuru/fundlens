"""XIRR (compute-engine.md §2), matching Excel's XIRR() day-count convention
(actual/365, discounted from the first cash-flow date) to within the
accuracy NFR: within 0.01% of Excel's XIRR on identical cash flows.

Pure, no I/O — cash-flow construction (which raw_transaction rows become
which signed flows) is the caller's job (compute/metrics.py), same
separation as the lot allocator.
"""

import math
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from scipy.optimize import brentq, newton

_LOWER_BOUND = -0.9999
_UPPER_BOUND = 10.0
_NEWTON_SEED = 0.1
_TOLERANCE = 1e-9


@dataclass(frozen=True)
class CashFlow:
    date: date
    amount: Decimal  # negative = outflow (money invested), positive = inflow


@dataclass(frozen=True)
class XirrResult:
    rate: float | None  # annualized, e.g. 0.145 == 14.5%
    reason: str | None  # set only when rate is None


def _objective(rate: float, flows: list[tuple[float, float]]) -> float:
    base = 1.0 + rate
    if base <= 0:
        # A negative base raised to a fractional exponent has no real value.
        # numpy silently returns NaN (with a RuntimeWarning) for this rather
        # than raising, which — left unguarded — can let a NaN "solution"
        # slip through brentq/newton undetected. Raising here instead makes
        # both solvers correctly treat this rate as outside their domain.
        raise OverflowError("rate below -100% is outside the XIRR domain")
    return sum(amount / base**years for years, amount in flows)


def compute_xirr(cash_flows: list[CashFlow]) -> XirrResult:
    if len(cash_flows) < 2:
        return XirrResult(rate=None, reason="insufficient_flow_variation")

    has_negative = any(cf.amount < 0 for cf in cash_flows)
    has_positive = any(cf.amount > 0 for cf in cash_flows)
    if not (has_negative and has_positive):
        return XirrResult(rate=None, reason="insufficient_flow_variation")

    base_date = min(cf.date for cf in cash_flows)
    if all(cf.date == base_date for cf in cash_flows):
        return XirrResult(rate=None, reason="same_date_flows")

    flows = [
        ((cf.date - base_date).days / 365.0, float(cf.amount)) for cf in cash_flows
    ]

    try:
        f_lower = _objective(_LOWER_BOUND, flows)
        f_upper = _objective(_UPPER_BOUND, flows)
        if f_lower * f_upper < 0:
            rate = brentq(
                lambda r: _objective(r, flows), _LOWER_BOUND, _UPPER_BOUND, xtol=_TOLERANCE
            )
            if math.isfinite(rate):
                return XirrResult(rate=rate, reason=None)
    except (ValueError, OverflowError):
        pass

    try:
        rate = float(
            newton(lambda r: _objective(r, flows), x0=_NEWTON_SEED, tol=_TOLERANCE, maxiter=200)
        )
        if math.isfinite(rate):
            return XirrResult(rate=rate, reason=None)
        return XirrResult(rate=None, reason="solver_did_not_converge")
    except (RuntimeError, ValueError, OverflowError):
        return XirrResult(rate=None, reason="solver_did_not_converge")
