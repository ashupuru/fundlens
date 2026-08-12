from datetime import date
from decimal import Decimal

from fundlens.market_data.amfi_client import parse_navall, parse_navall_categories

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
