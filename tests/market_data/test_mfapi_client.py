from datetime import date
from decimal import Decimal

from fundlens.market_data.mfapi_client import parse_scheme_history

# Real-shaped payload from api.mfapi.in/mf/119551 (2026-08), trimmed.
REAL_SHAPED_PAYLOAD = {
    "meta": {
        "fund_house": "Aditya Birla Sun Life Mutual Fund",
        "scheme_code": 119551,
        "scheme_name": "Aditya Birla Sun Life Banking & PSU Debt Fund  - DIRECT - IDCW",
    },
    "data": [
        {"date": "11-08-2026", "nav": "107.17660"},
        {"date": "10-08-2026", "nav": "107.19580"},
        {"date": "07-08-2026", "nav": "107.09710"},
    ],
}


def test_parses_full_history() -> None:
    rows = parse_scheme_history("119551", REAL_SHAPED_PAYLOAD)

    assert len(rows) == 3
    assert all(r.amfi_code == "119551" for r in rows)


def test_date_format_is_day_month_year_not_month_day_year() -> None:
    """mfapi's date format is DD-MM-YYYY, unlike AMFI's DD-Mon-YYYY. Using
    the wrong day/month order here would silently corrupt every NAV date for
    dates where day <= 12."""
    rows = parse_scheme_history("119551", REAL_SHAPED_PAYLOAD)

    dates = {r.nav_date for r in rows}
    assert date(2026, 8, 11) in dates
    assert date(2026, 8, 10) in dates
    assert date(2026, 8, 7) in dates


def test_nav_values_parsed_as_decimal() -> None:
    rows = parse_scheme_history("119551", REAL_SHAPED_PAYLOAD)

    row = next(r for r in rows if r.nav_date == date(2026, 8, 11))
    assert row.nav == Decimal("107.17660")


def test_missing_data_key_produces_no_rows() -> None:
    assert parse_scheme_history("119551", {"meta": {}}) == []


def test_malformed_entry_is_skipped_not_raised() -> None:
    payload = {"data": [{"date": "not-a-date", "nav": "107.17660"}]}
    assert parse_scheme_history("119551", payload) == []
