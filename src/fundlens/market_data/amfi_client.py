"""AMFI NAVAll feed: a bulk, semicolon-delimited dump of every scheme's
*latest* NAV in one file. Used for the daily bulk refresh (PRD FR-1.4).

Real format, verified live against the feed (2026-08):

    Scheme Code;ISIN Div Payout/ ISIN Growth;ISIN Div Reinvestment;Scheme Name;Net Asset Value;Date


    Open Ended Schemes(Debt Scheme - Banking and PSU Fund)

    Aditya Birla Sun Life Mutual Fund

    119551;INF209KA12Z1;INF209KA13Z9;<scheme name>;107.1766;11-Aug-2026

Interspersed with blank lines, AMC-name header lines, and category header
lines (all of which lack the semicolon-delimited 6-field shape) — the parser
below distinguishes data rows purely structurally (6 fields, numeric scheme
code), rather than trying to track which section it's in.

Note: the PRD names `www.amfiindia.com/spages/NAVAll.txt`, which now
redirects (HTTP 301, confirmed live) to `portal.amfiindia.com/spages/NAVAll.txt`.
"""

from datetime import date
from decimal import Decimal, InvalidOperation

import httpx
from dateutil import parser as date_parser

from fundlens.market_data.records import NavRow

NAVALL_URL = "https://portal.amfiindia.com/spages/NAVAll.txt"


def fetch_navall_text(timeout_seconds: float = 30.0) -> str:
    response = httpx.get(NAVALL_URL, timeout=timeout_seconds, follow_redirects=True)
    response.raise_for_status()
    return response.text


def parse_navall(text: str) -> list[NavRow]:
    rows: list[NavRow] = []
    for line in text.splitlines():
        fields = line.split(";")
        if len(fields) != 6:
            continue

        amfi_code = fields[0].strip()
        if not amfi_code.isdigit():
            continue

        nav_str = fields[4].strip()
        date_str = fields[5].strip()
        try:
            nav = Decimal(nav_str)
        except InvalidOperation:
            continue  # e.g. "N.A." for schemes with no current NAV

        nav_date = _parse_date(date_str)
        if nav_date is None:
            continue

        rows.append(NavRow(amfi_code=amfi_code, nav_date=nav_date, nav=nav))

    return rows


def _parse_date(value: str) -> date | None:
    try:
        return date_parser.parse(value).date()
    except (ValueError, TypeError):
        return None
