import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy.orm import Session

from fundlens.compute import service
from fundlens.compute.data_quality import (
    get_coverage_gaps,
    get_data_freshness,
    get_failed_xirr_positions,
    get_folio_summary,
    get_unmatched_schemes,
)
from fundlens.db.models import (
    CoverageGapRecord,
    DataFreshness,
    FetchStatus,
    NavSource,
    RawCASUpload,
    RawTransaction,
    TransactionType,
    UploadStatus,
)


def _make_upload(session: Session, source_filename: str | None = "cas.pdf") -> RawCASUpload:
    upload = RawCASUpload(
        file_hash=f"hash-{uuid.uuid4().hex}",
        source_filename=source_filename,
        parser_version="test",
        status=UploadStatus.PARSED,
    )
    session.add(upload)
    session.flush()
    return upload


def _add_txn(
    session: Session,
    upload_id: str,
    *,
    folio: str = "F1",
    amc: str = "Test AMC",
    pan: str | None = "ABCDE1234F",
    scheme_name: str = "Fund A",
    amfi_code: str | None = "111",
    isin: str | None = "ISIN111",
    txn_type: TransactionType,
    txn_date: date,
    sequence: int = 0,
    units: Decimal | None,
    nav: Decimal | None = None,
    amount_paise: int | None = None,
) -> None:
    session.add(
        RawTransaction(
            upload_id=upload_id,
            folio=folio,
            amc=amc,
            pan=pan,
            scheme_name=scheme_name,
            amfi_code=amfi_code,
            isin=isin,
            txn_type=txn_type,
            txn_date=txn_date,
            sequence=sequence,
            units=units,
            nav=nav,
            amount_paise=amount_paise,
            natural_key=f"key-{uuid.uuid4().hex}",
        )
    )


def test_folio_summary_empty_when_no_transactions(session: Session) -> None:
    assert get_folio_summary(session) == []


def test_folio_summary_aggregates_across_schemes_and_dates(session: Session) -> None:
    upload = _make_upload(session)
    _add_txn(
        session, upload.id, folio="F1", scheme_name="Fund A", amfi_code="111", isin="ISIN111",
        txn_type=TransactionType.PURCHASE, txn_date=date(2022, 1, 1), sequence=0,
        units=Decimal("100"), nav=Decimal("10"), amount_paise=100_000,
    )
    _add_txn(
        session, upload.id, folio="F1", scheme_name="Fund B", amfi_code="222", isin="ISIN222",
        txn_type=TransactionType.PURCHASE, txn_date=date(2023, 6, 1), sequence=0,
        units=Decimal("50"), nav=Decimal("20"), amount_paise=100_000,
    )
    session.commit()

    summaries = get_folio_summary(session)

    assert len(summaries) == 1
    s = summaries[0]
    assert s.folio == "F1"
    assert s.amc == "Test AMC"
    assert s.pan == "ABCDE1234F"
    assert s.scheme_count == 2
    assert s.txn_count == 2
    assert s.first_txn_date == date(2022, 1, 1)
    assert s.last_txn_date == date(2023, 6, 1)


def test_folio_summary_sorted_by_folio_and_pan_falls_back_to_none(session: Session) -> None:
    upload = _make_upload(session)
    _add_txn(
        session, upload.id, folio="F2", pan=None, txn_type=TransactionType.PURCHASE,
        txn_date=date(2022, 1, 1), units=Decimal("10"),
    )
    _add_txn(
        session, upload.id, folio="F1", pan="ABCDE1234F", txn_type=TransactionType.PURCHASE,
        txn_date=date(2022, 1, 1), units=Decimal("10"),
    )
    session.commit()

    summaries = get_folio_summary(session)

    assert [s.folio for s in summaries] == ["F1", "F2"]
    assert summaries[1].pan is None


def test_coverage_gaps_empty_when_none_recorded(session: Session) -> None:
    assert get_coverage_gaps(session) == []


def test_coverage_gaps_reflects_persisted_records(session: Session) -> None:
    upload = _make_upload(session, source_filename="jan.pdf")
    session.add(
        CoverageGapRecord(
            upload_id=upload.id,
            folio="F1",
            scheme_name="Fund A",
            opening_balance_units=Decimal("42.5"),
            message="42.5 units already held at the start of the statement period",
        )
    )
    session.commit()

    gaps = get_coverage_gaps(session)

    assert len(gaps) == 1
    gap = gaps[0]
    assert gap.folio == "F1"
    assert gap.scheme_name == "Fund A"
    assert gap.opening_balance_units == Decimal("42.5")
    assert gap.source_filename == "jan.pdf"
    assert isinstance(gap.detected_at, datetime)


def test_unmatched_schemes_empty_when_all_holdings_resolved(session: Session) -> None:
    upload = _make_upload(session)
    _add_txn(
        session, upload.id, txn_type=TransactionType.PURCHASE, txn_date=date(2022, 1, 1),
        units=Decimal("100"), nav=Decimal("10"), amount_paise=100_000,
    )
    session.commit()
    service.rebuild_all_lots(session)

    assert get_unmatched_schemes(session) == []


def test_unmatched_schemes_flags_holding_missing_amfi_code(session: Session) -> None:
    upload = _make_upload(session)
    _add_txn(
        session, upload.id, amfi_code=None, isin="ISIN999",
        txn_type=TransactionType.PURCHASE, txn_date=date(2022, 1, 1),
        units=Decimal("100"), nav=Decimal("10"), amount_paise=100_000,
    )
    session.commit()
    service.rebuild_all_lots(session)

    unmatched = get_unmatched_schemes(session)

    assert len(unmatched) == 1
    assert unmatched[0].amfi_code is None


def test_failed_xirr_positions_flags_same_date_flows(session: Session) -> None:
    upload = _make_upload(session)
    _add_txn(
        session, upload.id, txn_type=TransactionType.PURCHASE, txn_date=date(2024, 1, 1),
        units=Decimal("100"), nav=Decimal("10"), amount_paise=100_000,
    )
    _add_txn(
        session, upload.id, sequence=1, txn_type=TransactionType.REDEMPTION,
        txn_date=date(2024, 1, 1), units=Decimal("-100"), nav=Decimal("10"),
        amount_paise=-100_000,
    )
    session.commit()
    service.rebuild_all_lots(session)

    failed = get_failed_xirr_positions(session)

    assert len(failed) == 1
    assert failed[0].xirr_reason == "same_date_flows"


def test_data_freshness_empty_when_never_fetched(session: Session) -> None:
    assert get_data_freshness(session) == []


def test_data_freshness_reflects_persisted_row(session: Session) -> None:
    session.add(
        DataFreshness(
            source=NavSource.AMFI_NAVALL,
            last_attempt_at=datetime(2026, 1, 1, 9, 0, 0),
            last_success_at=datetime(2026, 1, 1, 9, 0, 0),
            status=FetchStatus.OK,
            rows_updated=5,
        )
    )
    session.commit()

    freshness = get_data_freshness(session)

    assert len(freshness) == 1
    assert freshness[0].source == "amfi_navall"
    assert freshness[0].status == "ok"
    assert freshness[0].rows_updated == 5


def _dup_txn(
    session: Session,
    *,
    folio: str,
    parser_version: str,
    amount_paise: int,
    units: Decimal,
    txn_date: date = date(2021, 10, 6),
    txn_type: TransactionType = TransactionType.PURCHASE,
    amfi_code: str = "120700",
) -> None:
    upload = RawCASUpload(
        file_hash=f"h-{uuid.uuid4().hex}",
        parser_version=parser_version,
        status=UploadStatus.PARSED,
    )
    session.add(upload)
    session.flush()
    session.add(
        RawTransaction(
            upload_id=upload.id,
            folio=folio,
            amc="ICICI",
            scheme_name="Acme Hybrid",
            isin="INF001",
            amfi_code=amfi_code,
            txn_type=txn_type,
            txn_date=txn_date,
            units=units,
            amount_paise=amount_paise,
            natural_key=f"k-{uuid.uuid4().hex}",
        )
    )
    session.commit()


def test_cross_source_duplicate_found_despite_differing_amounts(session: Session) -> None:
    """Real shape of the bug: identical units, amounts a few paise apart
    because the CAS is net of stamp duty and the broker reports gross."""
    from fundlens.compute.data_quality import get_cross_source_duplicates

    _dup_txn(session, folio="AMC-1", parser_version="1.3.0",
             amount_paise=4999750, units=Decimal("379.9850"))
    _dup_txn(session, folio="ZERODHA", parser_version="zerodha-tradebook-csv-v1",
             amount_paise=4999744, units=Decimal("379.9850"))

    dups = get_cross_source_duplicates(session)

    assert len(dups) == 1
    assert dups[0].units == Decimal("379.9850")
    assert {r.source for r in dups[0].rows} == {"Direct / AMC folio", "Zerodha"}
    # The broker copy is removable; the CAS copy is kept.
    assert [r.folio for r in dups[0].removable] == ["ZERODHA"]
    assert dups[0].keep is not None and dups[0].keep.folio == "AMC-1"


def test_repeats_within_one_folio_are_not_flagged(session: Session) -> None:
    from fundlens.compute.data_quality import get_cross_source_duplicates

    for _ in range(2):
        _dup_txn(session, folio="AMC-1", parser_version="1.3.0",
                 amount_paise=100_000, units=Decimal("10.0000"))

    assert get_cross_source_duplicates(session) == []


def test_different_units_are_not_duplicates(session: Session) -> None:
    from fundlens.compute.data_quality import get_cross_source_duplicates

    _dup_txn(session, folio="AMC-1", parser_version="1.3.0",
             amount_paise=100_000, units=Decimal("10.0000"))
    _dup_txn(session, folio="ZERODHA", parser_version="zerodha-tradebook-csv-v1",
             amount_paise=100_000, units=Decimal("11.0000"))

    assert get_cross_source_duplicates(session) == []


def test_two_non_broker_sources_are_not_auto_resolvable(session: Session) -> None:
    """Without a broker side there's no basis to prefer one copy over the
    other, so nothing is marked removable."""
    from fundlens.compute.data_quality import get_cross_source_duplicates

    _dup_txn(session, folio="AMC-1", parser_version="1.3.0",
             amount_paise=100_000, units=Decimal("10.0000"))
    _dup_txn(session, folio="AMC-2", parser_version="1.3.0",
             amount_paise=100_000, units=Decimal("10.0000"))

    assert get_cross_source_duplicates(session) == []


def test_tax_lines_without_units_are_never_duplicates(session: Session) -> None:
    """Zero-unit rows (stamp duty, STT) would otherwise all collide with each
    other on the same date."""
    from fundlens.compute.data_quality import get_cross_source_duplicates

    for folio, pv in [("AMC-1", "1.3.0"), ("ZERODHA", "zerodha-tradebook-csv-v1")]:
        _dup_txn(session, folio=folio, parser_version=pv, amount_paise=187,
                 units=Decimal("0"), txn_type=TransactionType.STAMP_DUTY_TAX)

    assert get_cross_source_duplicates(session) == []
