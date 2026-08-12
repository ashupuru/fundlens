"""Data quality surfacing (PRD §5.4/P1, pulled forward to P0.6): folios
found, historical-coverage gaps, unresolved scheme identities, and failed
XIRR computations — so trust in P0's numbers has visible provenance from
day one, not silently assumed. See development-plan.md P0.6.
"""

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from fundlens.compute.holdings import HoldingView, get_current_holdings
from fundlens.compute.metrics import HoldingMetrics, get_all_holding_metrics
from fundlens.db.models import CoverageGapRecord, DataFreshness, RawTransaction
from fundlens.scheme_identity import scheme_key


@dataclass(frozen=True)
class FolioSummary:
    folio: str
    amc: str
    pan: str | None
    scheme_count: int
    txn_count: int
    first_txn_date: date
    last_txn_date: date


def get_folio_summary(session: Session) -> list[FolioSummary]:
    txns = session.scalars(select(RawTransaction)).all()

    by_folio: dict[str, list[RawTransaction]] = {}
    for txn in txns:
        by_folio.setdefault(txn.folio, []).append(txn)

    summaries = [
        FolioSummary(
            folio=folio,
            amc=rows[0].amc,
            pan=next((r.pan for r in rows if r.pan), None),
            scheme_count=len({scheme_key(r.isin, r.amfi_code, r.scheme_name) for r in rows}),
            txn_count=len(rows),
            first_txn_date=min(r.txn_date for r in rows),
            last_txn_date=max(r.txn_date for r in rows),
        )
        for folio, rows in by_folio.items()
    ]
    return sorted(summaries, key=lambda s: s.folio)


@dataclass(frozen=True)
class CoverageGapView:
    folio: str
    scheme_name: str
    opening_balance_units: Decimal
    message: str
    source_filename: str | None
    detected_at: datetime


def get_coverage_gaps(session: Session) -> list[CoverageGapView]:
    rows = session.scalars(select(CoverageGapRecord)).all()
    return [
        CoverageGapView(
            folio=r.folio,
            scheme_name=r.scheme_name,
            opening_balance_units=r.opening_balance_units,
            message=r.message,
            source_filename=r.upload.source_filename,
            detected_at=r.detected_at,
        )
        for r in rows
    ]


def get_unmatched_schemes(session: Session) -> list[HoldingView]:
    """Currently-open holdings still missing an ISIN or AMFI code — NAV
    lookups and tax classification can't resolve for these (compute-engine.md
    §3.1, data-model.md scheme_identity)."""
    return [h for h in get_current_holdings(session) if h.isin is None or h.amfi_code is None]


def get_failed_xirr_positions(session: Session) -> list[HoldingMetrics]:
    return [m for m in get_all_holding_metrics(session) if m.xirr is None]


@dataclass(frozen=True)
class DataFreshnessView:
    source: str
    last_attempt_at: datetime
    last_success_at: datetime | None
    status: str
    error_message: str | None
    rows_updated: int | None


def get_data_freshness(session: Session) -> list[DataFreshnessView]:
    rows = session.scalars(select(DataFreshness)).all()
    return [
        DataFreshnessView(
            source=r.source.value,
            last_attempt_at=r.last_attempt_at,
            last_success_at=r.last_success_at,
            status=r.status.value,
            error_message=r.error_message,
            rows_updated=r.rows_updated,
        )
        for r in rows
    ]
