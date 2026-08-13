from decimal import Decimal

import pytest

from fundlens.market_data.groww_client import parse_groww_screener, total_results

# Trimmed real-shaped snippet from groww.in's public mutual-fund screener API
# (verified live, 2026-08): GET /v1/api/search/v3/query/filter_derived_data/
# st_filter?page=0&size=N&q=&sortBy=0 -- public fund data, no auth needed.
REAL_SHAPED_PAYLOAD = {
    "content": [
        {
            "scheme_code": "119788",
            "scheme_name": "SBI Gold Direct Plan Growth",
            "fund_house": "SBI Mutual Fund",
            "category": "Commodities",
            "sub_category": "Gold",
            "plan_type": "Direct",
            "aum": 15811.8654,
            "groww_rating": 4,
            "risk_rating": 5,
            "expense_ratio": "0.24",
            "fund_manager": "Raviprakash Sharma",
            "exit_load": "Exit load of 1% if redeemed within 15 days.",
            "return1y": 52.6,
            "return3y": 36.04,
            "return5y": 25.6,
            "return10y": 16.25,
            "sip_return1y": 27.2928129612087,
            "sip_return3y": 40.9276694660877,
            "sip_return5y": 32.287807082681155,
            "launch_date": "2013-03-12",
        },
        {
            "scheme_code": "147946",
            "scheme_name": "Bandhan Small Cap Fund Direct Growth",
            "fund_house": "IDFC Mutual Fund",
            "category": "Equity",
            "sub_category": "Small Cap",
            "plan_type": "Direct",
            "aum": 31103.0294,
            "groww_rating": 5,
            "risk_rating": 6,
            "expense_ratio": "0.57",
            "fund_manager": "Anoop Bhaskar",
            "exit_load": "Exit load of 1%, if redeemed within 1 year.",
            "return1y": 14.44,
            "return3y": 27.22,
            "return5y": 20.31,
            "return10y": None,
            "sip_return1y": 24.08758180070885,
            "sip_return3y": 17.760582236055882,
            "sip_return5y": 23.76619042388219,
            "launch_date": "2022-02-25",
        },
        # Real-data-verified edge case: Groww's own listing has records
        # sharing the same scheme_code (e.g. an old vs current fund name) --
        # not a parser concern, but this fixture includes a record with no
        # scheme_code at all, which the parser must skip (can't join it).
        {"scheme_name": "Some Fund With No Code", "aum": 100.0},
        {"scheme_code": "999999"},  # no scheme_name either -- also skipped
    ],
    "total_results": 3414,
}


def test_parses_valid_records() -> None:
    rows = parse_groww_screener(REAL_SHAPED_PAYLOAD)

    assert len(rows) == 2
    first = rows[0]
    assert first.amfi_code == "119788"
    assert first.scheme_name == "SBI Gold Direct Plan Growth"
    assert first.aum == Decimal("15811.8654")
    assert first.groww_rating == 4
    assert first.risk_rating == 5
    assert first.expense_ratio == Decimal("0.24")
    assert first.return_1y == Decimal("52.6")
    assert first.sip_return_1y == Decimal("27.2928129612087")


def test_records_missing_scheme_code_or_name_are_skipped() -> None:
    rows = parse_groww_screener(REAL_SHAPED_PAYLOAD)
    codes = {r.amfi_code for r in rows}

    assert "999999" not in codes
    assert len(rows) == 2


def test_null_return_field_parses_as_none_not_zero() -> None:
    rows = parse_groww_screener(REAL_SHAPED_PAYLOAD)
    bandhan = next(r for r in rows if r.amfi_code == "147946")

    assert bandhan.return_10y is None


def test_total_results() -> None:
    assert total_results(REAL_SHAPED_PAYLOAD) == 3414


def test_unexpected_payload_shape_raises_value_error() -> None:
    with pytest.raises(ValueError, match="unexpected"):
        parse_groww_screener({"unexpected": "shape"})


def test_empty_content_produces_no_rows() -> None:
    assert parse_groww_screener({"content": [], "total_results": 0}) == []
