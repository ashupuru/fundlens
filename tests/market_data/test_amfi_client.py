from datetime import date
from decimal import Decimal

from fundlens.compute.plan_audit import PlanType, detect_plan_type
from fundlens.ingestion.zerodha_matching import resolve_isin
from fundlens.market_data.amfi_client import (
    parse_navall,
    parse_navall_categories,
    parse_navall_scheme_master,
)

# Trimmed real snippet from portal.amfiindia.com/spages/NAVAll.txt (2026-08),
# including the header row, blank lines, an AMC-name header, a category
# header, and an "N.A." NAV edge case — all public market data.
REAL_SHAPED_SNIPPET = """Scheme Code;ISIN Div Payout/ ISIN Growth;ISIN Div Reinvestment;Scheme Name;Net Asset Value;Date

Open Ended Schemes(Debt Scheme - Banking and PSU Fund)

Aditya Birla Sun Life Mutual Fund

119551;INF209KA12Z1;INF209KA13Z9;Aditya Birla Sun Life Banking & PSU Debt Fund  - DIRECT - IDCW;107.1766;11-Aug-2026
108273;INF209K01LV0;-;Aditya Birla Sun Life Banking & PSU Debt Fund - Regular Plan-Growth;388.5291;11-Aug-2026
999999;-;-;Some Suspended Scheme;N.A.;11-Aug-2026
"""


def test_parses_valid_data_rows() -> None:
    rows = parse_navall(REAL_SHAPED_SNIPPET)

    assert len(rows) == 2  # the N.A. row is excluded
    first = rows[0]
    assert first.amfi_code == "119551"
    assert first.nav == Decimal("107.1766")
    assert first.nav_date == date(2026, 8, 11)


def test_skips_header_and_blank_and_category_lines() -> None:
    rows = parse_navall(REAL_SHAPED_SNIPPET)
    codes = {r.amfi_code for r in rows}

    assert codes == {"119551", "108273"}


def test_skips_rows_with_non_numeric_nav() -> None:
    rows = parse_navall(REAL_SHAPED_SNIPPET)

    assert "999999" not in {r.amfi_code for r in rows}


def test_empty_text_produces_no_rows() -> None:
    assert parse_navall("") == []


def test_malformed_line_with_wrong_field_count_is_skipped() -> None:
    text = "119551;INF209KA12Z1;only three fields\n"
    assert parse_navall(text) == []


# Real snippet spanning two category sections plus a nested-paren category
# header (ETFs) — proves the header regex isn't confused by the extra parens.
MULTI_CATEGORY_SNIPPET = """Scheme Code;ISIN Div Payout/ ISIN Growth;ISIN Div Reinvestment;Scheme Name;Net Asset Value;Date

Open Ended Schemes(Debt Scheme - Banking and PSU Fund)

Aditya Birla Sun Life Mutual Fund

119551;INF209KA12Z1;INF209KA13Z9;Aditya Birla Sun Life Banking & PSU Debt Fund  - DIRECT - IDCW;107.1766;11-Aug-2026

Open Ended Schemes(Exchange Traded Funds (ETFs) - Equity ETF)

Some ETF AMC

222222;INF000ETF001;-;Some Equity ETF;250.0000;11-Aug-2026

Open Ended Schemes(Equity Scheme - Large Cap Fund)

Another AMC

333333;INF000LC0001;-;Some Large Cap Fund;100.0000;11-Aug-2026
"""


def test_categories_tag_correctly_across_multiple_sections() -> None:
    rows = parse_navall_categories(MULTI_CATEGORY_SNIPPET)
    by_code = {r.amfi_code: r.category_raw for r in rows}

    assert by_code["119551"] == "Open Ended Schemes(Debt Scheme - Banking and PSU Fund)"
    assert by_code["333333"] == "Open Ended Schemes(Equity Scheme - Large Cap Fund)"


def test_nested_parens_in_etf_category_header_do_not_break_parsing() -> None:
    rows = parse_navall_categories(MULTI_CATEGORY_SNIPPET)
    by_code = {r.amfi_code: r.category_raw for r in rows}

    assert by_code["222222"] == "Open Ended Schemes(Exchange Traded Funds (ETFs) - Equity ETF)"


def test_category_row_count_matches_nav_row_count() -> None:
    nav_rows = parse_navall(MULTI_CATEGORY_SNIPPET)
    category_rows = parse_navall_categories(MULTI_CATEGORY_SNIPPET)

    assert len(nav_rows) == len(category_rows) == 3


def test_data_row_before_any_category_header_is_skipped() -> None:
    text = "119551;INF209KA12Z1;INF209KA13Z9;Some Fund;107.1766;11-Aug-2026\n"
    assert parse_navall_categories(text) == []


def test_empty_text_produces_no_category_rows() -> None:
    assert parse_navall_categories("") == []


# --- AMFI's August 2026 layout change ----------------------------------------
#
# Two columns (Plan, Option) were inserted before the NAV and the scheme-name
# column was stripped of the plan/option text it used to carry. Every parser
# keyed on a 6-field shape, so all three silently returned zero rows from a
# healthy 1.5 MB download: broker imports reported `no_candidates` for every
# row and the NAV refresh logged `status=OK, rows_updated=0` daily.
#
# Trimmed real snippet, verified live 2026-08-19. The empty Plan/Option pair on
# the last row is also real -- AMFI leaves those blank for ~6,300 legacy
# schemes.
CURRENT_FORMAT_SNIPPET = (
    "Scheme Code;ISIN Div Payout/ ISIN Growth;ISIN Div Reinvestment;"
    "Scheme Name;Plan;Option;Net Asset Value;Date\n"
    "\n"
    "Open Ended Schemes(Debt Scheme - Banking and PSU Fund)\n"
    "\n"
    "Aditya Birla Sun Life Mutual Fund\n"
    "\n"
    "119551;INF209KA12Z1;INF209KA13Z9;Aditya Birla Sun Life Banking & PSU Debt Fund;"
    "Direct Plan;IDCW-Re-investment;107.1683;19-Aug-2026\n"
    "108273;INF209K01LV0;-;Aditya Birla Sun Life Banking & PSU Debt Fund;"
    "Regular Plan;Growth;388.5291;19-Aug-2026\n"
    "127042;INF247L01445;-;Motilal Oswal Midcap Fund;;;104.2100;19-Aug-2026\n"
    "999999;-;-;Some Suspended Scheme;Direct Plan;Growth;N.A.;19-Aug-2026\n"
)


def test_current_eight_field_format_parses_navs() -> None:
    """The regression itself: these rows returned nothing before the fix."""
    rows = parse_navall(CURRENT_FORMAT_SNIPPET)

    assert len(rows) == 3  # the N.A. row is excluded
    assert rows[0].amfi_code == "119551"
    assert rows[0].nav == Decimal("107.1683")  # field 6, not field 4
    assert rows[0].nav_date == date(2026, 8, 19)  # field 7, not field 5


def test_current_format_recomposes_plan_and_option_into_the_scheme_name() -> None:
    """Load-bearing, not cosmetic: `detect_plan_type` reads Direct/Regular out
    of the name string, so a bare name would turn every newly-imported holding
    UNKNOWN."""
    rows = parse_navall_scheme_master(CURRENT_FORMAT_SNIPPET)
    by_code = {r.amfi_code: r for r in rows}

    assert by_code["119551"].scheme_name == (
        "Aditya Birla Sun Life Banking & PSU Debt Fund - Direct Plan - IDCW-Re-investment"
    )
    assert detect_plan_type(by_code["119551"].scheme_name) == PlanType.DIRECT
    assert detect_plan_type(by_code["108273"].scheme_name) == PlanType.REGULAR


def test_blank_plan_and_option_leave_a_bare_name_rather_than_dangling_separators() -> None:
    """AMFI leaves Plan/Option empty for ~6,300 legacy schemes. The name must
    not come back as "Fund - - "."""
    rows = parse_navall_scheme_master(CURRENT_FORMAT_SNIPPET)
    motilal = next(r for r in rows if r.amfi_code == "127042")

    assert motilal.scheme_name == "Motilal Oswal Midcap Fund"


def test_isins_still_resolve_under_the_current_format() -> None:
    rows = parse_navall_scheme_master(CURRENT_FORMAT_SNIPPET)
    result = resolve_isin("INF209KA12Z1", rows)

    assert result.matched is not None
    assert result.matched.amfi_code == "119551"


def test_legacy_six_field_format_still_parses() -> None:
    """Kept accepted so cached text and existing fixtures keep working, and so
    an upstream revert doesn't break us a second time."""
    assert len(parse_navall(REAL_SHAPED_SNIPPET)) == 2
    assert len(parse_navall_scheme_master(REAL_SHAPED_SNIPPET)) == 3


def test_categories_parse_under_the_current_format() -> None:
    rows = parse_navall_categories(CURRENT_FORMAT_SNIPPET)

    assert len(rows) == 4  # every numeric-code data row, N.A. included
    assert all(r.category_raw.startswith("Open Ended Schemes") for r in rows)
