"""ORM models for the raw, immutable ingestion layer (data-model.md §2)."""

import enum
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import Boolean, Date, DateTime, Enum, ForeignKey, Integer, Numeric, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from fundlens.tax.asset_class import TaxAssetClass


class Base(DeclarativeBase):
    pass


class UploadStatus(enum.StrEnum):
    PARSED = "parsed"
    PARSE_FAILED = "parse_failed"
    SUPERSEDED = "superseded"


class TransactionType(enum.StrEnum):
    """Mirrors casparser.types.TransactionType exactly (verified against v1.3.0)."""

    PURCHASE = "PURCHASE"
    PURCHASE_SIP = "PURCHASE_SIP"
    REDEMPTION = "REDEMPTION"
    DIVIDEND_PAYOUT = "DIVIDEND_PAYOUT"
    DIVIDEND_REINVEST = "DIVIDEND_REINVEST"
    SWITCH_IN = "SWITCH_IN"
    SWITCH_IN_MERGER = "SWITCH_IN_MERGER"
    SWITCH_OUT = "SWITCH_OUT"
    SWITCH_OUT_MERGER = "SWITCH_OUT_MERGER"
    STT_TAX = "STT_TAX"
    STAMP_DUTY_TAX = "STAMP_DUTY_TAX"
    TDS_TAX = "TDS_TAX"
    SEGREGATION = "SEGREGATION"
    GIFT_IN = "GIFT_IN"
    GIFT_OUT = "GIFT_OUT"
    MISC = "MISC"
    UNKNOWN = "UNKNOWN"
    REVERSAL = "REVERSAL"


def _uuid() -> str:
    return str(uuid.uuid4())


class RawCASUpload(Base):
    __tablename__ = "raw_cas_upload"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    file_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    source_filename: Mapped[str | None] = mapped_column(String(255))
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(UTC).replace(tzinfo=None)
    )
    statement_period_start: Mapped[date | None] = mapped_column(Date)
    statement_period_end: Mapped[date | None] = mapped_column(Date)
    parser_version: Mapped[str] = mapped_column(String(20))
    status: Mapped[UploadStatus] = mapped_column(Enum(UploadStatus))

    transactions: Mapped[list["RawTransaction"]] = relationship(back_populates="upload")


class RawTransaction(Base):
    __tablename__ = "raw_transaction"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    upload_id: Mapped[str] = mapped_column(ForeignKey("raw_cas_upload.id"), index=True)

    pan: Mapped[str | None] = mapped_column(String(10))
    folio: Mapped[str] = mapped_column(String(64), index=True)
    amc: Mapped[str] = mapped_column(String(255))

    scheme_name: Mapped[str] = mapped_column(String(255))
    isin: Mapped[str | None] = mapped_column(String(12))
    amfi_code: Mapped[str | None] = mapped_column(String(20))

    txn_type: Mapped[TransactionType] = mapped_column(Enum(TransactionType))
    txn_date: Mapped[date] = mapped_column(Date, index=True)
    # Position within this scheme's transaction list, in the stable order
    # casparser/the CSV importer emits them (0-indexed, per (folio, scheme)).
    # FIFO lot ordering needs a same-day tiebreaker (compute-engine.md §1),
    # and balance_after can't serve that role — it moves in opposite
    # directions for inflows vs outflows, so it can't order a mixed same-day
    # sequence.
    sequence: Mapped[int] = mapped_column(default=0)
    description: Mapped[str | None] = mapped_column(String(255))

    units: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))
    nav: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    amount_paise: Mapped[int | None] = mapped_column()
    balance_after: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))
    dividend_rate: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    gift_folio: Mapped[str | None] = mapped_column(String(64))

    natural_key: Mapped[str] = mapped_column(String(64), unique=True, index=True)

    upload: Mapped["RawCASUpload"] = relationship(back_populates="transactions")


class NavSource(enum.StrEnum):
    AMFI_NAVALL = "amfi_navall"
    MFAPI = "mfapi"
    MANUAL = "manual"


class NavHistory(Base):
    """One row per (scheme, date) NAV. Append-only — see data-model.md §2."""

    __tablename__ = "nav_history"

    amfi_code: Mapped[str] = mapped_column(String(20), primary_key=True)
    nav_date: Mapped[date] = mapped_column(Date, primary_key=True)
    nav: Mapped[Decimal] = mapped_column(Numeric(12, 4))
    source: Mapped[NavSource] = mapped_column(Enum(NavSource))
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(UTC).replace(tzinfo=None)
    )


class FetchStatus(enum.StrEnum):
    OK = "ok"
    FAILED = "failed"


class DataFreshness(Base):
    """One row per market-data source, upserted on every fetch attempt.

    Backs the "stale-data banner, never silent" NFR (technical-spec.md §7):
    the UI reads this table rather than trusting the last cached NAV as
    implicitly current.
    """

    __tablename__ = "data_freshness"

    source: Mapped[NavSource] = mapped_column(Enum(NavSource), primary_key=True)
    last_attempt_at: Mapped[datetime] = mapped_column(DateTime)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime)
    status: Mapped[FetchStatus] = mapped_column(Enum(FetchStatus))
    error_message: Mapped[str | None] = mapped_column(String(500))
    rows_updated: Mapped[int | None] = mapped_column()


class BenchmarkId(enum.StrEnum):
    """FR-1.4/FR-4: the four benchmark indices the PRD names by name.
    "Category-appropriate debt indices" (also named in FR-1.4) is not
    represented here — deferred, see compute-engine.md §4."""

    NIFTY50_TRI = "NIFTY50_TRI"
    BSE100_TRI = "BSE100_TRI"
    NIFTY_MIDCAP150_TRI = "NIFTY_MIDCAP150_TRI"
    NIFTY_SMALLCAP250_TRI = "NIFTY_SMALLCAP250_TRI"


class BenchmarkHistory(Base):
    """One row per (benchmark, date) value. Same append-only, insert-if-absent
    shape as nav_history (data-model.md §2).

    `proxy`/`proxy_note`: PRD §10 names true TRI licensed data as hard to
    source freely; live-verified during P1.1 (NSE/niftyindices block
    unauthenticated fetches with 403/503). Every row here is therefore a
    proxy — an index fund's actual NAV standing in for its benchmark's TRI —
    per the PRD's own documented fallback, never presented as true TRI
    without this flag (benchmark/proxy_config.py has the real scheme
    mapping).
    """

    __tablename__ = "benchmark_history"

    benchmark_id: Mapped[BenchmarkId] = mapped_column(Enum(BenchmarkId), primary_key=True)
    nav_date: Mapped[date] = mapped_column(Date, primary_key=True)
    value: Mapped[Decimal] = mapped_column(Numeric(20, 4))
    proxy: Mapped[bool] = mapped_column(Boolean, default=True)
    proxy_note: Mapped[str | None] = mapped_column(String(255))
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(UTC).replace(tzinfo=None)
    )


class SchemeMetadata(Base):
    """Scheme category, refreshed alongside NAV during the AMFI bulk fetch
    (market_data/service.py). `tax_asset_class` is derived from
    `category_raw` at write time (tax/asset_class.py) — see that module's
    docstring for the full real-data-grounded classification taxonomy.
    """

    __tablename__ = "scheme_metadata"

    amfi_code: Mapped[str] = mapped_column(String(20), primary_key=True)
    category_raw: Mapped[str] = mapped_column(String(255))
    tax_asset_class: Mapped[TaxAssetClass] = mapped_column(Enum(TaxAssetClass))
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(UTC).replace(tzinfo=None)
    )


class CoverageGapRecord(Base):
    """Persisted mirror of ingestion.records.CoverageGap (data-model.md §2):
    a scheme whose CAS statement opened with a nonzero balance, meaning that
    upload's transaction history doesn't cover the scheme's full lifetime.

    Detected at ingest time (ingestion/service.py) and persisted per-upload
    so the data quality dashboard (P0.6) can show it after the fact, not
    only in the immediate ingest API response.
    """

    __tablename__ = "coverage_gap"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    upload_id: Mapped[str] = mapped_column(ForeignKey("raw_cas_upload.id"), index=True)
    folio: Mapped[str] = mapped_column(String(64), index=True)
    scheme_name: Mapped[str] = mapped_column(String(255))
    opening_balance_units: Mapped[Decimal] = mapped_column(Numeric(20, 4))
    message: Mapped[str] = mapped_column(String(500))
    detected_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(UTC).replace(tzinfo=None)
    )

    upload: Mapped["RawCASUpload"] = relationship()


class Lot(Base):
    """FIFO cost-basis lot (compute-engine.md §1, data-model.md §3).

    Derived, rebuildable state: regenerated wholesale for a folio whenever
    its transactions change, never patched in place — see compute/service.py.
    """

    __tablename__ = "lot"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    folio: Mapped[str] = mapped_column(String(64), index=True)
    scheme_key: Mapped[str] = mapped_column(String(255), index=True)

    open_date: Mapped[date] = mapped_column(Date)
    units_remaining: Mapped[Decimal] = mapped_column(Numeric(20, 4))
    cost_per_unit: Mapped[Decimal] = mapped_column(Numeric(20, 4))
    source_txn_id: Mapped[str] = mapped_column(ForeignKey("raw_transaction.id"))
    source_txn_type: Mapped[TransactionType] = mapped_column(Enum(TransactionType))
    close_date: Mapped[date | None] = mapped_column(Date)
    zero_cost_flag: Mapped[bool] = mapped_column(Boolean, default=False)


class RealizedGain(Base):
    """One row per FIFO consumption slice — a single lot may be split across
    several of these if more than one outflow consumes from it over time,
    or a single outflow may span several of these if it drains more than
    one lot. Feeds the tax engine (P1.2) directly.
    """

    __tablename__ = "realized_gain"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    folio: Mapped[str] = mapped_column(String(64), index=True)
    scheme_key: Mapped[str] = mapped_column(String(255), index=True)

    source_txn_id: Mapped[str] = mapped_column(ForeignKey("raw_transaction.id"))
    open_date: Mapped[date] = mapped_column(Date)
    sale_txn_id: Mapped[str] = mapped_column(ForeignKey("raw_transaction.id"))
    sale_date: Mapped[date] = mapped_column(Date)

    units_consumed: Mapped[Decimal] = mapped_column(Numeric(20, 4))
    cost_per_unit: Mapped[Decimal] = mapped_column(Numeric(20, 4))
    sale_price_per_unit: Mapped[Decimal] = mapped_column(Numeric(20, 4))
    realized_gain_paise: Mapped[int] = mapped_column(Integer)
    holding_period_days: Mapped[int] = mapped_column(Integer)


class AssumptionVersion(Base):
    """PRD §5.7: tax thresholds, exemption limits, etc. as versioned,
    editable config rather than code constants — a report generated under
    one version stays reproducible even after the user edits an assumption
    later (report.assumption_version_id, data-model.md `report`).

    Exactly one row has `is_current=True` at a time — the version new
    reports use unless a specific historical version is requested.
    Creating a new version never mutates an existing one (assumption/service.py
    clones the current version's values, applies overrides, and flips the
    `is_current` flag) — old versions stay byte-for-byte reproducible.
    """

    __tablename__ = "assumption_version"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    label: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(UTC).replace(tzinfo=None)
    )
    is_current: Mapped[bool] = mapped_column(Boolean, default=False)


class Assumption(Base):
    """One (key, value) row per assumption per version. `value` is always
    text — typed parsing happens at the read site (assumption/service.py),
    since the set of keys/types is small and known, not worth a polymorphic
    column scheme for a single-user local app."""

    __tablename__ = "assumption"

    version_id: Mapped[str] = mapped_column(
        ForeignKey("assumption_version.id"), primary_key=True
    )
    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[str] = mapped_column(String(255))
