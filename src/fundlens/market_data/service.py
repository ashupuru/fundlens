"""Orchestrates NAV refresh: backfill new schemes via mfapi.in, bulk-refresh
the latest NAV for all held schemes via AMFI NAVAll, and track freshness per
source so the UI can render a stale-data banner instead of trusting silently
stale numbers (technical-spec.md §7).

Never raises on a fetch failure — every path degrades to a recorded
`DataFreshness(status=FAILED)` row and an empty/zero result, since a NAV
feed being down must never crash the app (PRD §6 Reliability NFR).
"""

from dataclasses import dataclass
from datetime import UTC, date, datetime

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from fundlens.db.models import DataFreshness, FetchStatus, NavHistory, NavSource, RawTransaction
from fundlens.market_data.amfi_client import fetch_navall_text, parse_navall
from fundlens.market_data.mfapi_client import fetch_scheme_history, parse_scheme_history
from fundlens.market_data.records import NavRow


@dataclass(frozen=True)
class RefreshSummary:
    backfilled_schemes: int
    backfilled_rows: int
    amfi_refresh_rows: int
    amfi_refresh_ok: bool
    amfi_error: str | None


def get_held_amfi_codes(session: Session) -> set[str]:
    codes = session.scalars(
        select(RawTransaction.amfi_code).where(RawTransaction.amfi_code.is_not(None)).distinct()
    ).all()
    return {c for c in codes if c}


def _insert_new_rows(session: Session, rows: list[NavRow], source: NavSource) -> int:
    if not rows:
        return 0

    existing: set[tuple[str, date]] = set(
        session.execute(
            select(NavHistory.amfi_code, NavHistory.nav_date).where(
                NavHistory.amfi_code.in_({r.amfi_code for r in rows})
            )
        )
        .tuples()
        .all()
    )

    inserted = 0
    fetched_at = datetime.now(UTC).replace(tzinfo=None)
    for row in rows:
        if (row.amfi_code, row.nav_date) in existing:
            continue
        session.add(
            NavHistory(
                amfi_code=row.amfi_code,
                nav_date=row.nav_date,
                nav=row.nav,
                source=source,
                fetched_at=fetched_at,
            )
        )
        existing.add((row.amfi_code, row.nav_date))  # guard within-batch duplicates too
        inserted += 1

    return inserted


def _record_freshness(
    session: Session,
    source: NavSource,
    status: FetchStatus,
    rows_updated: int | None,
    error_message: str | None,
) -> None:
    now = datetime.now(UTC).replace(tzinfo=None)
    freshness = session.get(DataFreshness, source)
    if freshness is None:
        freshness = DataFreshness(source=source, last_attempt_at=now, status=status)
        session.add(freshness)
    freshness.last_attempt_at = now
    freshness.status = status
    freshness.rows_updated = rows_updated
    freshness.error_message = error_message
    if status == FetchStatus.OK:
        freshness.last_success_at = now


def backfill_scheme_via_mfapi(session: Session, amfi_code: str) -> int:
    try:
        payload = fetch_scheme_history(amfi_code)
    except (httpx.HTTPError, ValueError) as exc:
        _record_freshness(session, NavSource.MFAPI, FetchStatus.FAILED, None, str(exc))
        session.commit()
        return 0

    rows = parse_scheme_history(amfi_code, payload)
    inserted = _insert_new_rows(session, rows, NavSource.MFAPI)
    _record_freshness(session, NavSource.MFAPI, FetchStatus.OK, inserted, None)
    session.commit()
    return inserted


def refresh_latest_via_amfi(session: Session, held_amfi_codes: set[str]) -> tuple[int, str | None]:
    if not held_amfi_codes:
        return 0, None  # nothing to look up — skip the (large) fetch entirely

    try:
        text = fetch_navall_text()
    except httpx.HTTPError as exc:
        _record_freshness(session, NavSource.AMFI_NAVALL, FetchStatus.FAILED, None, str(exc))
        session.commit()
        return 0, str(exc)

    all_rows = parse_navall(text)
    relevant_rows = [r for r in all_rows if r.amfi_code in held_amfi_codes]
    inserted = _insert_new_rows(session, relevant_rows, NavSource.AMFI_NAVALL)
    _record_freshness(session, NavSource.AMFI_NAVALL, FetchStatus.OK, inserted, None)
    session.commit()
    return inserted, None


def refresh_all_held_schemes(session: Session) -> RefreshSummary:
    held_codes = get_held_amfi_codes(session)

    schemes_with_history = set(
        session.scalars(
            select(NavHistory.amfi_code).where(NavHistory.amfi_code.in_(held_codes)).distinct()
        ).all()
    )
    schemes_needing_backfill = held_codes - schemes_with_history

    backfilled_rows = 0
    for code in schemes_needing_backfill:
        backfilled_rows += backfill_scheme_via_mfapi(session, code)

    amfi_rows, amfi_error = refresh_latest_via_amfi(session, held_codes)

    return RefreshSummary(
        backfilled_schemes=len(schemes_needing_backfill),
        backfilled_rows=backfilled_rows,
        amfi_refresh_rows=amfi_rows,
        amfi_refresh_ok=amfi_error is None,
        amfi_error=amfi_error,
    )
