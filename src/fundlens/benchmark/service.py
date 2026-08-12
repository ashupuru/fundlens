"""Backfills benchmark_history from each benchmark's proxy index fund via
mfapi.in (same fetcher P0.2 uses for held-scheme backfill — reused as-is,
since a proxy fund's full NAV history is fetched exactly the same way as
any other scheme's). Never raises on fetch failure, matching
market_data/service.py's degrade-to-recorded-failure pattern (PRD §6
Reliability NFR).
"""

from dataclasses import dataclass
from datetime import UTC, date, datetime

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from fundlens.benchmark.proxy_config import PROXY_NOTE_BY_BENCHMARK, PROXY_SCHEME_BY_BENCHMARK
from fundlens.db.models import BenchmarkHistory, BenchmarkId, FetchStatus, NavSource
from fundlens.market_data.mfapi_client import fetch_scheme_history, parse_scheme_history
from fundlens.market_data.records import NavRow
from fundlens.market_data.service import record_freshness


@dataclass(frozen=True)
class BenchmarkRefreshResult:
    benchmark_id: BenchmarkId
    inserted: int
    error: str | None


def _insert_new_benchmark_rows(
    session: Session, benchmark_id: BenchmarkId, rows: list[NavRow]
) -> int:
    if not rows:
        return 0

    existing: set[date] = set(
        session.scalars(
            select(BenchmarkHistory.nav_date).where(BenchmarkHistory.benchmark_id == benchmark_id)
        ).all()
    )

    proxy_note = PROXY_NOTE_BY_BENCHMARK[benchmark_id]
    fetched_at = datetime.now(UTC).replace(tzinfo=None)
    inserted = 0
    for row in rows:
        if row.nav_date in existing:
            continue
        session.add(
            BenchmarkHistory(
                benchmark_id=benchmark_id,
                nav_date=row.nav_date,
                value=row.nav,
                proxy=True,
                proxy_note=proxy_note,
                fetched_at=fetched_at,
            )
        )
        existing.add(row.nav_date)
        inserted += 1

    return inserted


def backfill_benchmark_history(
    session: Session, benchmark_id: BenchmarkId
) -> BenchmarkRefreshResult:
    amfi_code = PROXY_SCHEME_BY_BENCHMARK[benchmark_id]
    try:
        payload = fetch_scheme_history(amfi_code)
    except (httpx.HTTPError, ValueError) as exc:
        record_freshness(session, NavSource.MFAPI, FetchStatus.FAILED, None, str(exc))
        session.commit()
        return BenchmarkRefreshResult(benchmark_id=benchmark_id, inserted=0, error=str(exc))

    rows = parse_scheme_history(amfi_code, payload)
    inserted = _insert_new_benchmark_rows(session, benchmark_id, rows)
    record_freshness(session, NavSource.MFAPI, FetchStatus.OK, inserted, None)
    session.commit()
    return BenchmarkRefreshResult(benchmark_id=benchmark_id, inserted=inserted, error=None)


def refresh_all_benchmarks(session: Session) -> list[BenchmarkRefreshResult]:
    return [backfill_benchmark_history(session, benchmark_id) for benchmark_id in BenchmarkId]
