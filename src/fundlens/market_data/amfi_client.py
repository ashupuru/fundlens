"""AMFI NAVAll feed: a bulk, semicolon-delimited dump of every scheme's
*latest* NAV in one file. Used for the daily bulk refresh (PRD FR-1.4).

**AMFI changed this format in August 2026** and the change is silent and
breaking: two columns (`Plan`, `Option`) were inserted before the NAV, and
the scheme-name column was stripped of the plan/option text it used to
carry. Every parser here keyed on a 6-field shape, so all three began
returning zero rows from a perfectly healthy 1.5 MB download — Zerodha and
Dezerv imports reported `no_candidates` for every row, and the NAV refresh
recorded `status=OK, rows_updated=0` daily.

Current format (8 fields), verified live 2026-08-19:

    Scheme Code;ISIN Div Payout/ ISIN Growth;ISIN Div Reinvestment;
    Scheme Name;Plan;Option;Net Asset Value;Date

    119551;INF209KA12Z1;INF209KA13Z9;Aditya Birla Sun Life Banking & PSU
    Debt Fund;Direct Plan;IDCW-Re-investment;107.1683;19-Aug-2026

Legacy format (6 fields), still accepted so cached/fixture text keeps
parsing and a revert upstream doesn't break us again:

    119551;INF209KA12Z1;INF209KA13Z9;<name incl. plan & option>;107.1766;11-Aug-2026

The 8-field rows recompose `scheme_name` as "name - plan - option". That is
deliberate, not cosmetic: `compute/plan_audit.py::detect_plan_type` reads
"Direct"/"Regular" out of the *name string*, and scheme identity plus
Dezerv's fuzzy name matching were both built against names of the old
shape. Handing them a bare name would silently turn every newly-imported
holding's plan type into UNKNOWN.

Interspersed with blank lines, AMC-name header lines, and category header
lines (none of which have the semicolon-delimited shape) — the parser
distinguishes data rows purely structurally (known field count, numeric
scheme code), rather than tracking which section it's in.

Note: the PRD names `www.amfiindia.com/spages/NAVAll.txt`, which now
redirects (HTTP 301, confirmed live) to `portal.amfiindia.com/spages/NAVAll.txt`.
"""

import re
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import NamedTuple

import httpx
from dateutil import parser as date_parser

from fundlens.market_data.records import NavRow, SchemeCategoryRow, SchemeMasterRow

NAVALL_URL = "https://portal.amfiindia.com/spages/NAVAll.txt"

# A category header line, e.g. "Open Ended Schemes(Equity Scheme - Large Cap
# Fund)". Verified live (2026-08) that every such line starts with one of
# these three prefixes and is the only line shape containing "Schemes(" —
# distinguishing it from data rows (which never contain a literal "(").
_CATEGORY_HEADER_RE = re.compile(r"^(Open|Close|Interval)[^;]*\([^;]*\)\s*$")


# AMFI's Aug-2026 layout change (see module docstring). Both shapes are
# accepted; the field count is what identifies which one a line is.
_LEGACY_FIELD_COUNT = 6
_CURRENT_FIELD_COUNT = 8


class _DataLine(NamedTuple):
    amfi_code: str
    isin_growth: str | None
    isin_div_reinvestment: str | None
    scheme_name: str
    nav_str: str
    date_str: str


def _split_data_line(line: str) -> _DataLine | None:
    """One scheme row, or None for the blank/AMC/category lines that make up
    the rest of the file. The single place that knows the column layout."""
    fields = line.split(";")
    if len(fields) == _CURRENT_FIELD_COUNT:
        name_parts, nav_str, date_str = fields[3:6], fields[6], fields[7]
    elif len(fields) == _LEGACY_FIELD_COUNT:
        name_parts, nav_str, date_str = fields[3:4], fields[4], fields[5]
    else:
        return None

    amfi_code = fields[0].strip()
    if not amfi_code.isdigit():
        return None

    scheme_name = " - ".join(part.strip() for part in name_parts if part.strip())
    if not scheme_name:
        return None

    def _isin(value: str) -> str | None:
        cleaned = value.strip()
        return None if cleaned in ("", "-") else cleaned

    return _DataLine(
        amfi_code=amfi_code,
        isin_growth=_isin(fields[1]),
        isin_div_reinvestment=_isin(fields[2]),
        scheme_name=scheme_name,
        nav_str=nav_str.strip(),
        date_str=date_str.strip(),
    )


def fetch_navall_text(timeout_seconds: float = 30.0) -> str:
    response = httpx.get(NAVALL_URL, timeout=timeout_seconds, follow_redirects=True)
    response.raise_for_status()
    return response.text


def parse_navall(text: str) -> list[NavRow]:
    rows: list[NavRow] = []
    for line in text.splitlines():
        parsed = _split_data_line(line)
        if parsed is None:
            continue

        try:
            nav = Decimal(parsed.nav_str)
        except InvalidOperation:
            continue  # e.g. "N.A." for schemes with no current NAV

        nav_date = _parse_date(parsed.date_str)
        if nav_date is None:
            continue

        rows.append(NavRow(amfi_code=parsed.amfi_code, nav_date=nav_date, nav=nav))

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

        parsed = _split_data_line(line)
        if parsed is None:
            continue
        if current_category is None:
            continue  # defensive: a data row before any header is seen

        rows.append(
            SchemeCategoryRow(amfi_code=parsed.amfi_code, category_raw=current_category)
        )

    return rows


def parse_navall_scheme_master(text: str) -> list[SchemeMasterRow]:
    """Scheme identity (name + ISINs) alongside `parse_navall`'s NAV
    extraction, from the same already-fetched text — used to resolve a
    scheme referenced only by name (e.g. a third-party CSV export) against
    the real, current AMFI-listed universe (ingestion/dezerv_matching.py)."""
    rows: list[SchemeMasterRow] = []
    for line in text.splitlines():
        parsed = _split_data_line(line)
        if parsed is None:
            continue

        rows.append(
            SchemeMasterRow(
                amfi_code=parsed.amfi_code,
                scheme_name=parsed.scheme_name,
                isin_growth=parsed.isin_growth,
                isin_div_reinvestment=parsed.isin_div_reinvestment,
            )
        )

    return rows


def _parse_date(value: str) -> date | None:
    try:
        return date_parser.parse(value).date()
    except (ValueError, TypeError):
        return None
