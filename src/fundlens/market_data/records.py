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


@dataclass(frozen=True)
class SchemeMasterRow:
    """Scheme identity from the same NAVAll feed — amfi_code, name, and
    ISIN together, for resolving a scheme referenced by name only (e.g. a
    third-party CSV export) to a real, current AMFI-listed scheme."""

    amfi_code: str
    scheme_name: str
    isin_growth: str | None
    isin_div_reinvestment: str | None
