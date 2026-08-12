"""DB-independent NAV records produced by the market-data fetchers."""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal


@dataclass(frozen=True)
class NavRow:
    amfi_code: str
    nav_date: date
    nav: Decimal


@dataclass(frozen=True)
class SchemeCategoryRow:
    amfi_code: str
    # Verbatim AMFI section header, e.g.
    # "Open Ended Schemes(Equity Scheme - Large Cap Fund)"
    category_raw: str
