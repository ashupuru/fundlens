from datetime import date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from fundlens.benchmark.service import _insert_new_benchmark_rows, upsert_manual_tri_rows
from fundlens.benchmark.tri_csv_import import TRICSVValidationError, TRIRow, parse_tri_csv
from fundlens.db.models import BenchmarkHistory, BenchmarkId
from fundlens.market_data.records import NavRow


def test_parse_tri_csv_parses_valid_rows() -> None:
    content = "date,value\n2024-01-01,1000.0000\n2024-01-02,1005.5000\n"
    rows = parse_tri_csv(content)
    assert rows == [
        TRIRow(nav_date=date(2024, 1, 1), value=Decimal("1000.0000")),
        TRIRow(nav_date=date(2024, 1, 2), value=Decimal("1005.5000")),
    ]


def test_parse_tri_csv_skips_blank_lines() -> None:
    content = "date,value\n2024-01-01,1000\n\n2024-01-02,1005\n"
    rows = parse_tri_csv(content)
    assert len(rows) == 2


def test_parse_tri_csv_rejects_missing_columns() -> None:
    with pytest.raises(TRICSVValidationError, match="missing required columns"):
        parse_tri_csv("date\n2024-01-01\n")


def test_parse_tri_csv_rejects_malformed_row() -> None:
    with pytest.raises(TRICSVValidationError, match="Row 2"):
        parse_tri_csv("date,value\nnot-a-date,1000\n")


def test_upsert_manual_tri_rows_inserts_new_rows(session: Session) -> None:
    rows = [
        TRIRow(nav_date=date(2024, 1, 1), value=Decimal("1000")),
        TRIRow(nav_date=date(2024, 1, 2), value=Decimal("1010")),
    ]

    written = upsert_manual_tri_rows(session, BenchmarkId.NIFTY50_TRI, rows)
    session.commit()

    assert written == 2
    history = session.scalars(select(BenchmarkHistory)).all()
    assert len(history) == 2
    assert all(h.proxy is False for h in history)
    assert all(h.proxy_note is None for h in history)


def test_upsert_manual_tri_rows_overwrites_existing_proxy_row(session: Session) -> None:
    session.add(
        BenchmarkHistory(
            benchmark_id=BenchmarkId.NIFTY50_TRI,
            nav_date=date(2024, 1, 1),
            value=Decimal("900"),
            proxy=True,
            proxy_note="proxy fund NAV, not true TRI",
            fetched_at=datetime(2024, 1, 1, 0, 0, 0),
        )
    )
    session.commit()

    written = upsert_manual_tri_rows(
        session, BenchmarkId.NIFTY50_TRI, [TRIRow(nav_date=date(2024, 1, 1), value=Decimal("905"))]
    )
    session.commit()

    assert written == 1
    history = session.get(BenchmarkHistory, (BenchmarkId.NIFTY50_TRI, date(2024, 1, 1)))
    assert history is not None
    assert history.value == Decimal("905")
    assert history.proxy is False
    assert history.proxy_note is None


def test_upsert_manual_tri_rows_leaves_other_dates_untouched(session: Session) -> None:
    session.add(
        BenchmarkHistory(
            benchmark_id=BenchmarkId.NIFTY50_TRI,
            nav_date=date(2024, 1, 1),
            value=Decimal("900"),
            proxy=True,
            proxy_note="proxy fund NAV, not true TRI",
            fetched_at=datetime(2024, 1, 1, 0, 0, 0),
        )
    )
    session.commit()

    upsert_manual_tri_rows(
        session, BenchmarkId.NIFTY50_TRI, [TRIRow(nav_date=date(2024, 1, 2), value=Decimal("910"))]
    )
    session.commit()

    untouched = session.get(BenchmarkHistory, (BenchmarkId.NIFTY50_TRI, date(2024, 1, 1)))
    assert untouched is not None
    assert untouched.value == Decimal("900")
    assert untouched.proxy is True


def test_manual_tri_row_survives_subsequent_proxy_refresh(session: Session) -> None:
    upsert_manual_tri_rows(
        session, BenchmarkId.NIFTY50_TRI, [TRIRow(nav_date=date(2024, 1, 1), value=Decimal("905"))]
    )
    session.commit()

    # A later automatic refresh sees the same date with a (different) proxy
    # value -- insert-if-absent must never touch it.
    inserted = _insert_new_benchmark_rows(
        session,
        BenchmarkId.NIFTY50_TRI,
        [NavRow(amfi_code="999", nav_date=date(2024, 1, 1), nav=Decimal("899"))],
    )
    session.commit()

    assert inserted == 0
    history = session.get(BenchmarkHistory, (BenchmarkId.NIFTY50_TRI, date(2024, 1, 1)))
    assert history is not None
    assert history.value == Decimal("905")
    assert history.proxy is False
