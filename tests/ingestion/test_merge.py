from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from fundlens.db.models import RawCASUpload, RawTransaction, TransactionType, UploadStatus
from fundlens.ingestion.merge import merge_transactions
from fundlens.ingestion.natural_key import compute_natural_key
from fundlens.ingestion.records import TransactionRecord


def _make_record(**overrides: object) -> TransactionRecord:
    defaults: dict[str, object] = dict(
        pan="ABCDE1234F",
        folio="F1",
        amc="Test AMC",
        scheme_name="Test Fund",
        isin="ISIN1",
        amfi_code="123456",
        txn_type=TransactionType.PURCHASE,
        txn_date=date(2021, 4, 15),
        sequence=0,
        description="Purchase",
        units=Decimal("100.5"),
        nav=Decimal("49.75"),
        amount_paise=500_000,
        balance_after=Decimal("100.5"),
        dividend_rate=None,
        gift_folio=None,
    )
    defaults.update(overrides)
    if "natural_key" not in defaults:
        defaults["natural_key"] = compute_natural_key(
            folio=defaults["folio"],  # type: ignore[arg-type]
            scheme_identifier=defaults["isin"],  # type: ignore[arg-type]
            txn_date=defaults["txn_date"],  # type: ignore[arg-type]
            txn_type=defaults["txn_type"].value,  # type: ignore[union-attr]
            amount_paise=defaults["amount_paise"],  # type: ignore[arg-type]
            units=defaults["units"],  # type: ignore[arg-type]
        )
    return TransactionRecord(**defaults)  # type: ignore[arg-type]


def _make_upload(session: Session) -> RawCASUpload:
    upload = RawCASUpload(
        file_hash="hash1",
        parser_version="test",
        status=UploadStatus.PARSED,
    )
    session.add(upload)
    session.flush()
    return upload


def test_first_merge_inserts_all_records(session: Session) -> None:
    upload = _make_upload(session)
    records = [_make_record(), _make_record(folio="F2")]

    result = merge_transactions(session, upload.id, records)
    session.commit()

    assert result.inserted_count == 2
    assert result.duplicate_count == 0
    assert session.scalar(select(RawTransaction).limit(1)) is not None
    assert len(session.scalars(select(RawTransaction)).all()) == 2


def test_reupload_of_overlapping_records_inserts_nothing_new(session: Session) -> None:
    upload1 = _make_upload(session)
    records = [_make_record(), _make_record(folio="F2")]
    merge_transactions(session, upload1.id, records)
    session.commit()

    upload2 = RawCASUpload(file_hash="hash2", parser_version="test", status=UploadStatus.PARSED)
    session.add(upload2)
    session.flush()

    # Same two records plus one genuinely new one, simulating a newer CAS
    # that overlaps the previous upload's date range.
    overlapping_plus_new = [_make_record(), _make_record(folio="F2"), _make_record(folio="F3")]
    result = merge_transactions(session, upload2.id, overlapping_plus_new)
    session.commit()

    assert result.inserted_count == 1
    assert result.duplicate_count == 2
    assert len(session.scalars(select(RawTransaction)).all()) == 3


def test_duplicate_within_same_batch_is_inserted_once(session: Session) -> None:
    upload = _make_upload(session)
    same_record_twice = [_make_record(), _make_record()]

    result = merge_transactions(session, upload.id, same_record_twice)
    session.commit()

    assert result.inserted_count == 1
    assert result.duplicate_count == 1
    assert len(session.scalars(select(RawTransaction)).all()) == 1


def test_empty_batch_is_a_no_op(session: Session) -> None:
    upload = _make_upload(session)

    result = merge_transactions(session, upload.id, [])

    assert result.inserted_count == 0
    assert result.duplicate_count == 0
