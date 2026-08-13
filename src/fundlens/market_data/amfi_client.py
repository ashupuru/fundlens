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

import re
from datetime import date
from decimal import Decimal, InvalidOperation

import httpx
from dateutil import parser as date_parser

from fundlens.market_data.records import NavRow, SchemeCategoryRow, SchemeMasterRow

NAVALL_URL = "https://portal.amfiindia.com/spages/NAVAll.txt"

# A category header line, e.g. "Open Ended Schemes(Equity Scheme - Large Cap
# Fund)". Verified live (2026-08) that every such line starts with one of
# these three prefixes and is the only line shape containing "Schemes(" —
# distinguishing it from data rows (which never contain a literal "(").
_CATEGORY_HEADER_RE = re.compile(r"^(Open|Close|Interval)[^;]*\([^;]*\)\s*$")


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


def parse_navall_categories(text: str) -> list[SchemeCategoryRow]:
    """Tracks the current category header as it descends through the file's
    sections, tagging each data row it passes with that category. The
    schedule (category header, then a run of data rows until the next
    header) is the file's only real structure — every scheme falls under
    exactly one category header at parse time.
    """
    rows: list[SchemeCategoryRow] = []
    current_category: str | None = None

    for line in text.splitlines():
        stripped = line.strip()
        if _CATEGORY_HEADER_RE.match(stripped):
            current_category = stripped
            continue

        fields = line.split(";")
        if len(fields) != 6:
            continue
        amfi_code = fields[0].strip()
        if not amfi_code.isdigit():
            continue
        if current_category is None:
            continue  # defensive: a data row before any header is seen

        rows.append(SchemeCategoryRow(amfi_code=amfi_code, category_raw=current_category))

    return rows


def parse_navall_scheme_master(text: str) -> list[SchemeMasterRow]:
    """Scheme identity (name + ISINs) alongside `parse_navall`'s NAV
    extraction, from the same already-fetched text — used to resolve a
    scheme referenced only by name (e.g. a third-party CSV export) against
    the real, current AMFI-listed universe (ingestion/dezerv_matching.py)."""
    rows: list[SchemeMasterRow] = []
    for line in text.splitlines():
        fields = line.split(";")
        if len(fields) != 6:
            continue

        amfi_code = fields[0].strip()
        if not amfi_code.isdigit():
            continue

        scheme_name = fields[3].strip()
        if not scheme_name:
            continue

        isin_growth = fields[1].strip() or None
        isin_div_reinvestment = fields[2].strip() or None
        if isin_growth == "-":
            isin_growth = None
        if isin_div_reinvestment == "-":
            isin_div_reinvestment = None

        rows.append(
            SchemeMasterRow(
                amfi_code=amfi_code,
                scheme_name=scheme_name,
                isin_growth=isin_growth,
                isin_div_reinvestment=isin_div_reinvestment,
            )
        )

    return rows


def _parse_date(value: str) -> date | None:
    try:
        return date_parser.parse(value).date()
    except (ValueError, TypeError):
        return None
