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


@dataclass(frozen=True)
class GrowwFundRow:
    """One scheme's row from Groww's public mutual-fund screener — third-
    party, best-effort metadata (AUM, rating, expense ratio, trailing
    returns), joined to the rest of the app via `amfi_code` (verified live
    that Groww's own `scheme_code` field *is* the AMFI code)."""

    amfi_code: str
    scheme_name: str
    fund_house: str | None
    category: str | None
    sub_category: str | None
    plan_type: str | None
    aum: Decimal | None
    groww_rating: int | None
    risk_rating: int | None
    expense_ratio: Decimal | None
    fund_manager: str | None
    exit_load: str | None
    return_1y: Decimal | None
    return_3y: Decimal | None
    return_5y: Decimal | None
    return_10y: Decimal | None
    sip_return_1y: Decimal | None
    sip_return_3y: Decimal | None
    sip_return_5y: Decimal | None
    launch_date: date | None
