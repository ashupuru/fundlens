import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from fundlens.benchmark import service
from fundlens.db.models import BenchmarkHistory, BenchmarkId, DataFreshness, FetchStatus, NavSource

MFAPI_PAYLOAD = {
    "meta": {"scheme_code": 120716},
    "data": [
        {"date": "11-08-2026", "nav": "172.3168"},
        {"date": "10-08-2026", "nav": "171.9000"},
    ],
}


def test_backfill_benchmark_history_inserts_rows_and_records_freshness(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(service, "fetch_scheme_history", lambda code: MFAPI_PAYLOAD)

    result = service.backfill_benchmark_history(session, BenchmarkId.NIFTY50_TRI)

    assert result.inserted == 2
    assert result.error is None
    rows = session.scalars(select(BenchmarkHistory)).all()
    assert len(rows) == 2
    assert all(r.benchmark_id == BenchmarkId.NIFTY50_TRI for r in rows)
    assert all(r.proxy is True for r in rows)
    assert all(r.proxy_note for r in rows)

    freshness = session.get(DataFreshness, NavSource.MFAPI)
    assert freshness is not None
    assert freshness.status == FetchStatus.OK


def test_backfill_benchmark_history_is_idempotent(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(service, "fetch_scheme_history", lambda code: MFAPI_PAYLOAD)

    service.backfill_benchmark_history(session, BenchmarkId.NIFTY50_TRI)
    second = service.backfill_benchmark_history(session, BenchmarkId.NIFTY50_TRI)

    assert second.inserted == 0
    assert len(session.scalars(select(BenchmarkHistory)).all()) == 2


def test_backfill_benchmark_history_failure_degrades_gracefully(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    def raise_error(code: str) -> dict:
        raise httpx.ConnectError("connection failed")

    monkeypatch.setattr(service, "fetch_scheme_history", raise_error)

    result = service.backfill_benchmark_history(session, BenchmarkId.NIFTY50_TRI)

    assert result.inserted == 0
    assert result.error is not None
    assert session.scalars(select(BenchmarkHistory)).all() == []
    freshness = session.get(DataFreshness, NavSource.MFAPI)
    assert freshness is not None
    assert freshness.status == FetchStatus.FAILED


def test_refresh_all_benchmarks_covers_every_benchmark_id(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(service, "fetch_scheme_history", lambda code: MFAPI_PAYLOAD)

    results = service.refresh_all_benchmarks(session)

    assert {r.benchmark_id for r in results} == set(BenchmarkId)
