"""Fixed-point money helpers (data-model.md §1: amounts stored as integer
paise, never floats, to keep golden-file accuracy exact).
"""

from decimal import ROUND_HALF_UP, Decimal


def to_paise(amount: Decimal | None) -> int | None:
    if amount is None:
        return None
    return to_paise_strict(amount)


def to_paise_strict(amount: Decimal) -> int:
    return int((amount * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
