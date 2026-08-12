"""ORM models for the raw, immutable ingestion layer (data-model.md §2)."""

import enum
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import Date, DateTime, Enum, ForeignKey, Numeric, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


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
