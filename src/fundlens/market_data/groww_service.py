"""Refreshes groww_fund_metadata: a single-request, full-universe fetch of
Groww's public mutual-fund screener (market_data/groww_client.py), upserted
by amfi_code, current-state (not a time series) — same shape as
market_data/service.py's `_upsert_scheme_metadata`.

Never raises — matches market_data/service.py's "a feed being down must
never crash the app" convention: degrades to a recorded
`DataFreshness(status=FAILED)` row and an empty result.

(An earlier design here assumed Groww's screener needed ~170 paginated
requests and built a partial-failure-tolerant multi-page loop with a
politeness delay between requests — reverse-engineering the screener
page's actual JS bundle found the real API instead, which returns the
entire universe (~3,400 funds) in one request. That entire loop/delay/
partial-tolerance apparatus is gone; this is now a single fetch, same
shape as every other source in market_data/service.py.)
"""

from dataclasses import dataclass
from datetime import UTC, datetime

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from fundlens.db.models import FetchStatus, GrowwFundMetadata, NavSource
from fundlens.market_data.groww_client import fetch_groww_screener, parse_groww_screener
from fundlens.market_data.records import GrowwFundRow
from fundlens.market_data.service import record_freshness


@dataclass(frozen=True)
class GrowwRefreshSummary:
    rows_upserted: int
    ok: bool
    error: str | None


def _apply_row(metadata: GrowwFundMetadata, row: GrowwFundRow, fetched_at: datetime) -> None:
    metadata.scheme_name = row.scheme_name
    metadata.fund_house = row.fund_house
    metadata.category = row.category
    metadata.sub_category = row.sub_category
    metadata.plan_type = row.plan_type
    metadata.aum = row.aum
    metadata.groww_rating = row.groww_rating
    metadata.risk_rating = row.risk_rating
    metadata.expense_ratio = row.expense_ratio
    metadata.fund_manager = row.fund_manager
    metadata.exit_load = row.exit_load
    metadata.return_1y = row.return_1y
    metadata.return_3y = row.return_3y
    metadata.return_5y = row.return_5y
    metadata.return_10y = row.return_10y
    metadata.sip_return_1y = row.sip_return_1y
    metadata.sip_return_3y = row.sip_return_3y
    metadata.sip_return_5y = row.sip_return_5y
    metadata.launch_date = row.launch_date
    metadata.fetched_at = fetched_at


def _upsert_groww_metadata(session: Session, rows: list[GrowwFundRow]) -> int:
    if not rows:
        return 0

    now = datetime.now(UTC).replace(tzinfo=None)
    existing: dict[str, GrowwFundMetadata] = {
        m.amfi_code: m
        for m in session.scalars(
            select(GrowwFundMetadata).where(
                GrowwFundMetadata.amfi_code.in_({r.amfi_code for r in rows})
            )
        )
    }

    touched_codes: set[str] = set()
    for row in rows:
        # Groww's own listing carries duplicate scheme_codes (real-data-
        # verified: 252 of 3,414 rows in one fetch, e.g. the same fund
        # under both an old and current display name) -- `existing` is
        # updated as we go, not just seeded from the DB, so a later
        # duplicate in the same batch updates the same row instead of a
        # second INSERT colliding on the amfi_code primary key. Report the
        # count of distinct rows actually written, not raw records seen.
        metadata = existing.get(row.amfi_code)
        if metadata is None:
            metadata = GrowwFundMetadata(amfi_code=row.amfi_code)
            session.add(metadata)
            existing[row.amfi_code] = metadata
        _apply_row(metadata, row, now)
        touched_codes.add(row.amfi_code)

    return len(touched_codes)


def refresh_groww_metadata(session: Session) -> GrowwRefreshSummary:
    try:
        payload = fetch_groww_screener()
    except (httpx.HTTPError, ValueError) as exc:
        record_freshness(session, NavSource.GROWW_METADATA, FetchStatus.FAILED, None, str(exc))
        session.commit()
        return GrowwRefreshSummary(rows_upserted=0, ok=False, error=str(exc))

    try:
        rows = parse_groww_screener(payload)
    except ValueError as exc:
        record_freshness(session, NavSource.GROWW_METADATA, FetchStatus.FAILED, None, str(exc))
        session.commit()
        return GrowwRefreshSummary(rows_upserted=0, ok=False, error=str(exc))

    rows_upserted = _upsert_groww_metadata(session, rows)
    record_freshness(session, NavSource.GROWW_METADATA, FetchStatus.OK, rows_upserted, None)
    session.commit()

    return GrowwRefreshSummary(rows_upserted=rows_upserted, ok=True, error=None)
