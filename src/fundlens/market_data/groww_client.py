"""Groww's public mutual-fund screener JSON API: a third-party source of
fund metadata (AUM, star rating, risk rating, expense ratio, trailing
returns) that AMFI's own feeds don't carry. Best-effort only — see
groww_service.py for the refresh/upsert orchestration and its freshness
tracking.

Real endpoint, found by downloading and grepping the screener page's own
JS bundle (`/mutual-funds/filter`) for its API calls, since the page's
`pageNo` URL query param turned out to be inert (every value returned the
same first 10 records — pagination happens client-side against this JSON
API, driven by in-memory state, never reflected in the page URL):

    GET https://groww.in/v1/api/search/v3/query/filter_derived_data/st_filter
        ?page=0&size=N&q=&sortBy=0

Unauthenticated, no cookies/headers beyond a User-Agent needed. Response
is flat: `{"content": [...], "total_results": N}`. (The bundle also
references a sibling `mf_prime_screener/st_prime` endpoint gated to
`is_prime_investment_allowed` accounts — verified live that it returns
`{"content":[],"total_results":0}` unauthenticated; not used here.)

`size` is honored up to and past the real universe size — verified live
that `size=5000` against ~3,414 total funds simply returns all 3,414 in
one response, capped at the true count rather than erroring. That makes
the *entire* refresh a single HTTP request, not the ~170-request paginated
crawl an earlier (wrong) reading of the page-render endpoint implied.

`content[i].scheme_code` is the AMFI scheme code (verified against a real
scheme: `"scheme_code": "119788"` matches its AMFI code exactly) — this is
what makes joining Groww's data to the rest of the app a direct primary-key
match rather than the fuzzy name-matching problem in
ingestion/dezerv_matching.py.
"""

from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx
from dateutil import parser as date_parser

from fundlens.market_data.records import GrowwFundRow

_SCREENER_URL = "https://groww.in/v1/api/search/v3/query/filter_derived_data/st_filter"
# Comfortably above the real universe (~3,400 funds, 2026-08) so one
# request reliably returns everything; the API caps at the true count
# rather than erroring on an oversized `size` (verified live).
_FETCH_SIZE = 5000


def fetch_groww_screener(size: int = _FETCH_SIZE, timeout_seconds: float = 30.0) -> dict[str, Any]:
    response = httpx.get(
        _SCREENER_URL,
        params={"page": 0, "size": size, "q": "", "sortBy": 0},
        timeout=timeout_seconds,
        headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"},
    )
    response.raise_for_status()
    return response.json()  # type: ignore[no-any-return]


def _decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return None


def _int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _date(value: Any) -> date | None:
    if not value:
        return None
    try:
        return date_parser.parse(str(value)).date()
    except (ValueError, TypeError):
        return None


def parse_groww_screener(payload: dict[str, Any]) -> list[GrowwFundRow]:
    try:
        content = payload["content"]
    except (KeyError, TypeError) as exc:
        raise ValueError(f"unexpected Groww screener payload shape: {exc}") from exc

    rows: list[GrowwFundRow] = []
    for record in content:
        amfi_code = record.get("scheme_code")
        scheme_name = record.get("scheme_name")
        if not amfi_code or not scheme_name:
            continue  # can't join or display this row without either

        rows.append(
            GrowwFundRow(
                amfi_code=str(amfi_code),
                scheme_name=scheme_name,
                fund_house=record.get("fund_house"),
                category=record.get("category"),
                sub_category=record.get("sub_category"),
                plan_type=record.get("plan_type"),
                aum=_decimal(record.get("aum")),
                groww_rating=_int(record.get("groww_rating")),
                risk_rating=_int(record.get("risk_rating")),
                expense_ratio=_decimal(record.get("expense_ratio")),
                fund_manager=record.get("fund_manager"),
                exit_load=record.get("exit_load"),
                return_1y=_decimal(record.get("return1y")),
                return_3y=_decimal(record.get("return3y")),
                return_5y=_decimal(record.get("return5y")),
                return_10y=_decimal(record.get("return10y")),
                sip_return_1y=_decimal(record.get("sip_return1y")),
                sip_return_3y=_decimal(record.get("sip_return3y")),
                sip_return_5y=_decimal(record.get("sip_return5y")),
                launch_date=_date(record.get("launch_date")),
            )
        )

    return rows


def total_results(payload: dict[str, Any]) -> int:
    try:
        count = payload["total_results"]
    except (KeyError, TypeError) as exc:
        raise ValueError(f"unexpected Groww screener payload shape: {exc}") from exc
    return int(count)
