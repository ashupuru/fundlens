"""mfapi.in: a community-run, per-scheme API that returns a scheme's *full*
historical NAV series in one call. Used only to backfill a scheme's history
the first time it's encountered — not for routine daily refresh, since it's
one HTTP call per scheme (AMFI NAVAll's single bulk file is used for that).

Real response shape, verified live against the API (2026-08):

    GET https://api.mfapi.in/mf/{scheme_code}
    {
      "meta": {"fund_house": "...", "scheme_code": 119551, ...},
      "data": [
        {"date": "11-08-2026", "nav": "107.17660"},
        {"date": "10-08-2026", "nav": "107.19580"},
        ...
      ]
    }

`data` is newest-first; date format is DD-MM-YYYY (note: different from AMFI
NAVAll's DD-Mon-YYYY — both are parsed via dateutil rather than a hardcoded
strptime format, so this difference doesn't need special-casing).
"""

from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx
from dateutil import parser as date_parser

from fundlens.market_data.records import NavRow

MFAPI_BASE_URL = "https://api.mfapi.in/mf"


def fetch_scheme_history(amfi_code: str, timeout_seconds: float = 30.0) -> dict[str, Any]:
    response = httpx.get(f"{MFAPI_BASE_URL}/{amfi_code}", timeout=timeout_seconds)
    response.raise_for_status()
    payload: dict[str, Any] = response.json()
    return payload


def parse_scheme_history(amfi_code: str, payload: dict[str, Any]) -> list[NavRow]:
    rows: list[NavRow] = []
    for entry in payload.get("data", []):
        nav_date = _parse_date(entry.get("date", ""))
        if nav_date is None:
            continue
        try:
            nav = Decimal(entry.get("nav", ""))
        except InvalidOperation:
            continue
        rows.append(NavRow(amfi_code=amfi_code, nav_date=nav_date, nav=nav))
    return rows


def _parse_date(value: str) -> date | None:
    try:
        return date_parser.parse(value, dayfirst=True).date()
    except (ValueError, TypeError):
        return None
