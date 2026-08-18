from datetime import date

import pytest

from fundlens.ingestion.zerodha_csv import ZerodhaFormatError, parse_zerodha_csv

_HEADER = (
    "symbol,isin,trade_date,exchange,segment,series,trade_type,auction,quantity,price,"
    "trade_id,order_id,order_execution_time"
)


def _sample(rows: str) -> str:
    return _HEADER + "\n" + rows


def test_parses_real_row_shape_and_computes_amount_from_quantity_times_price() -> None:
    # A real row from the supplied tradebook-TV7840-MF.csv.
    content = _sample(
        "MIRAE ASSET FLEXI CAP FUND - DIRECT PLAN,INF769K01JJ2,2025-08-01,BSE,MF,,buy,false,"
        "62.337000,16.041000,2004399965,2004399965,2025-08-01T00:00:00\n"
    )
    result = parse_zerodha_csv(content)
    assert len(result.rows) == 1
    row = result.rows[0]
    assert row.isin == "INF769K01JJ2"
    assert row.symbol == "MIRAE ASSET FLEXI CAP FUND - DIRECT PLAN"
    assert row.trade_type == "buy"
    assert row.txn_date == date(2025, 8, 1)
    assert row.trade_id == "2004399965"
    # 62.337 * 16.041 = 999.94836... -> round-half-up to the nearest paisa.
    assert row.amount_paise == 99995


def test_non_mf_segment_rows_filtered_out() -> None:
    content = _sample(
        "RELIANCE,INE002A01018,2025-08-01,NSE,EQ,EQ,buy,false,10.000000,2500.000000,"
        "1,1,2025-08-01T00:00:00\n"
        "MIRAE ASSET FLEXI CAP FUND - DIRECT PLAN,INF769K01JJ2,2025-08-01,BSE,MF,,buy,false,"
        "62.337000,16.041000,2004399965,2004399965,2025-08-01T00:00:00\n"
    )
    result = parse_zerodha_csv(content)
    assert len(result.rows) == 1
    assert result.rows[0].isin == "INF769K01JJ2"


def test_sell_trade_type_recognized() -> None:
    content = _sample(
        "MIRAE ASSET FLEXI CAP FUND - DIRECT PLAN,INF769K01JJ2,2025-09-01,BSE,MF,,sell,false,"
        "10.000000,17.000000,999,999,2025-09-01T00:00:00\n"
    )
    result = parse_zerodha_csv(content)
    assert result.rows[0].trade_type == "sell"


def test_unrecognized_trade_type_flagged_not_guessed() -> None:
    content = _sample(
        "MIRAE ASSET FLEXI CAP FUND - DIRECT PLAN,INF769K01JJ2,2025-09-01,BSE,MF,,switch,false,"
        "10.000000,17.000000,999,999,2025-09-01T00:00:00\n"
    )
    result = parse_zerodha_csv(content)
    assert result.rows == []
    assert result.unrecognized_trade_types == {"switch"}


def test_missing_isin_counted_not_dropped_silently() -> None:
    content = _sample(
        "MIRAE ASSET FLEXI CAP FUND - DIRECT PLAN,,2025-09-01,BSE,MF,,buy,false,"
        "10.000000,17.000000,999,999,2025-09-01T00:00:00\n"
    )
    result = parse_zerodha_csv(content)
    assert result.rows == []
    assert result.rows_missing_isin == 1


def test_missing_header_raises_format_error() -> None:
    with pytest.raises(ZerodhaFormatError):
        parse_zerodha_csv("not,a,zerodha,file\n1,2,3,4\n")


def test_same_isin_different_symbol_text_over_time_both_parsed() -> None:
    # Real-data-verified instability: the same ISIN appears under two
    # different symbol strings across the file's date range.
    content = _sample(
        "MIRAE ASSET FLEXI CAP FUND - DIRECT PLAN,INF769K01JJ2,2025-08-01,BSE,MF,,buy,false,"
        "62.337000,16.041000,1,1,2025-08-01T00:00:00\n"
        "MIRAE ASSET FLEXI CAP FUND DIRECT PLAN - GROWTH,INF769K01JJ2,2025-08-07,BSE,MF,,buy,false,"
        "62.368000,16.033000,2,2,2025-08-07T00:00:00\n"
    )
    result = parse_zerodha_csv(content)
    assert len(result.rows) == 2
    assert {r.isin for r in result.rows} == {"INF769K01JJ2"}
    assert {r.symbol for r in result.rows} == {
        "MIRAE ASSET FLEXI CAP FUND - DIRECT PLAN",
        "MIRAE ASSET FLEXI CAP FUND DIRECT PLAN - GROWTH",
    }


def test_duplicate_looking_same_day_rows_all_kept_distinguished_by_trade_id() -> None:
    # Real data has multiple same-day, same-qty/price rows for one ISIN
    # (separate real fills) -- trade_id is the only thing that tells them
    # apart, and none must be dropped as a false "duplicate".
    content = _sample(
        "MIRAE ASSET FLEXI CAP FUND - DIRECT PLAN,INF769K01JJ2,2025-08-04,BSE,MF,,buy,false,"
        "61.916000,16.150000,2010313668,2010313668,2025-08-04T00:00:00\n"
        "MIRAE ASSET FLEXI CAP FUND - DIRECT PLAN,INF769K01JJ2,2025-08-04,BSE,MF,,buy,false,"
        "61.916000,16.150000,2010217308,2010217308,2025-08-04T00:00:00\n"
    )
    result = parse_zerodha_csv(content)
    assert len(result.rows) == 2
    assert {r.trade_id for r in result.rows} == {"2010313668", "2010217308"}
