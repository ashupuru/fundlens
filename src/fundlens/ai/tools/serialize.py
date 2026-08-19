"""Turning compute dataclasses into citable evidence.

Money is the subtle one. The determinism validator accepts a statement
only when the cited value *appears in the statement text* within a small
absolute tolerance (`ai/validator.py::_number_matches_text`), so exposing
a corpus as `242257727` paise would make every naturally-written sentence
about it unciteable -- nobody writes "your corpus is 242257727". Indian
prose says "₹2.42 crore" or "₹242.26 lakh".

So every money field is emitted as all three units at once. The model
cites whichever one it actually wrote, and the check stays strict rather
than being loosened into uselessness. This follows the convention
`ai/snapshot.py` already set by exposing `fee_drag_annual_rupees` and
`aum_crores` rather than paise.
"""

from decimal import Decimal
from typing import Any


def money(paise: int | None) -> dict[str, float] | None:
    """One amount in the three units Indian financial prose actually uses."""
    if paise is None:
        return None
    rupees = paise / 100
    return {
        "rupees": round(rupees, 2),
        "lakh": round(rupees / 100_000, 2),
        "crore": round(rupees / 10_000_000, 3),
    }


def number(value: Decimal | float | None, digits: int = 2) -> float | None:
    if value is None:
        return None
    return round(float(value), digits)


def compact(data: dict[str, Any]) -> dict[str, Any]:
    """Drops keys whose value is None. Keeps the evidence small and stops
    the model citing a path that resolves to null -- which the validator
    cannot distinguish from a path that doesn't resolve at all."""
    return {k: v for k, v in data.items() if v is not None}
