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


@dataclass(frozen=True)
class DuplicateRow:
    """One side of a cross-source duplicate."""

    txn_id: str
    folio: str
    source: str
    amount_paise: int | None


@dataclass(frozen=True)
class CrossSourceDuplicate:
    """The same real transaction ingested twice under different folios.

    Identified by (amfi_code, txn_date, buy/sell direction, exact units)
    appearing under more than one folio with at least one broker-imported
    side. Keyed on units rather than amount because the sources disagree on
    amount by a few paise -- which is precisely why the original amount-only
    match in `ingestion/broker_import_shared.py` missed 113 of these.
    """

    amfi_code: str
    scheme_name: str
    txn_date: date
    direction: str
    units: Decimal
    rows: list[DuplicateRow]
    # The broker-side copies cleanup would delete -- the CAS records are
    # kept, being the richer ones (real AMC folio, stamp-duty lines, history
    # well beyond the broker export's window).
    #
    # A *list*, because a group can legitimately hold several identical rows:
    # three parallel Rs 1,000 SIPs into one fund on one day all buy the same
    # number of units, so they are indistinguishable. Removing a single row
    # per group left one duplicate behind on every such date in real data.
    removable: list[DuplicateRow]
    keep: DuplicateRow | None


def get_cross_source_duplicates(session: Session) -> list[CrossSourceDuplicate]:
    """Transactions present under more than one folio with identical units.

    Only *provable* duplicates: exact unit equality. Anything looser (near
    units, tolerant amounts) is indistinguishable from two genuinely
    separate same-day purchases, and under-reporting is recoverable where
    deleting real history is not.
    """
    from fundlens.compute.metrics import (
        BROKER_SOURCES,
        DIRECTION_BY_TXN_TYPE,
        derived_position_sources,
    )

    txns = session.scalars(select(RawTransaction)).all()
    # **Derived** sources, deliberately -- provenance is "which import
    # produced this row", and a user's `holding_source_override` says
    # something different ("where do I hold this fund"). Reading effective
    # sources here is self-defeating: resolving a duplicate writes an
    # override naming the broker onto the surviving CAS position, which
    # would then make that position look broker-sourced and hide any
    # remaining duplicates on it. Real data hit exactly that -- a second
    # pass reported zero while 49 duplicates were still present.
    sources = derived_position_sources(session)

    grouped: dict[tuple[str, date, str, Decimal], list[RawTransaction]] = {}
    for txn in txns:
        direction = DIRECTION_BY_TXN_TYPE.get(txn.txn_type)
        if txn.amfi_code is None or not txn.units or direction is None:
            continue
        grouped.setdefault(
            (txn.amfi_code, txn.txn_date, direction, abs(txn.units)), []
        ).append(txn)

    duplicates: list[CrossSourceDuplicate] = []
    for (amfi_code, txn_date, direction, units), rows in grouped.items():
        if len({r.folio for r in rows}) < 2:
            continue  # repeats inside one folio are genuine, not cross-source

        detailed = [
            DuplicateRow(
                txn_id=r.id,
                folio=r.folio,
                source=sources.get(
                    (r.folio, scheme_key(r.isin, r.amfi_code, r.scheme_name)), "Unknown"
                ),
                amount_paise=r.amount_paise,
            )
            for r in rows
        ]
        broker_rows = [d for d in detailed if d.source in BROKER_SOURCES]
        non_broker = [d for d in detailed if d.source not in BROKER_SOURCES]
        if not broker_rows or not non_broker:
            # Both sides from the same kind of import -- no basis to prefer
            # one over the other, so report nothing rather than guess.
            continue

        # Remove at most as many broker rows as the CAS itself accounts for.
        # If the broker reports MORE than the CAS (M > N), the surplus is not
        # a duplicate -- the CAS simply doesn't cover it (its statement window,
        # or a trade it never saw) -- and deleting it would destroy real
        # history. Capping at N keeps the count right in both directions.
        duplicates.append(
            CrossSourceDuplicate(
                amfi_code=amfi_code,
                scheme_name=rows[0].scheme_name,
                txn_date=txn_date,
                direction=direction,
                units=units,
                rows=detailed,
                removable=broker_rows[: len(non_broker)],
                keep=non_broker[0],
            )
        )

    return sorted(duplicates, key=lambda d: (d.scheme_name, d.txn_date))
