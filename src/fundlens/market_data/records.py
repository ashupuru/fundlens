"""DB-independent NAV records produced by the market-data fetchers."""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal


@dataclass(frozen=True)
class NavRow:
    amfi_code: str
    nav_date: date
    nav: Decimal
