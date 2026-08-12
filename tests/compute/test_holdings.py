import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy.orm import Session

from fundlens.compute import service
from fundlens.compute.holdings import get_current_holdings, get_holdings_by_amc
from fundlens.db.models import RawCASUpload, RawTransaction, TransactionType, UploadStatus


def _make_upload(session: Session) -> RawCASUpload:
    upload = RawCASUpload(
        file_hash=f"hash-{uuid.uuid4().hex}", parser_version="test", status=UploadStatus.PARSED
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
    scheme_name: str = "Fund A",
    amfi_code: str = "111",
    pan: str | None = "ABCDE1234F",
    txn_type: TransactionType = TransactionType.PURCHASE,
    txn_date: date,
    sequence: int = 0,
    units: Decimal | None,
    nav: Decimal | None = None,
) -> None:
    session.add(
        RawTransaction(
            upload_id=upload_id,
            folio=folio,
            amc=amc,
            scheme_name=scheme_name,
            amfi_code=amfi_code,
            pan=pan,
            txn_type=txn_type,
            txn_date=txn_date,
            sequence=sequence,
            units=units,
            nav=nav,
            natural_key=f"key-{uuid.uuid4().hex}",
        )
    )


def test_holdings_empty_when_no_lots(session: Session) -> None:
    assert get_current_holdings(session) == []


def test_holdings_reflects_open_lots_with_descriptive_fields(session: Session) -> None:
    upload = _make_upload(session)
    _add_txn(session, upload.id, txn_date=date(2021, 1, 1), units=Decimal("100"), nav=Decimal("10"))
    session.commit()
    service.rebuild_lots_for_folio(session, "F1")

    holdings = get_current_holdings(session)

    assert len(holdings) == 1
    h = holdings[0]
    assert h.folio == "F1"
    assert h.scheme_name == "Fund A"
    assert h.amc == "Test AMC"
    assert h.pan == "ABCDE1234F"
    assert h.units_remaining == Decimal("100")
    assert h.average_cost_per_unit == Decimal("10")
    assert h.cost_basis_paise == 100_000  # 100 units * 10 rupees = 1000 rupees


def test_fully_redeemed_scheme_does_not_appear_in_holdings(session: Session) -> None:
    upload = _make_upload(session)
    _add_txn(
        session, upload.id, txn_date=date(2021, 1, 1), sequence=0,
        units=Decimal("100"), nav=Decimal("10"),
    )
    _add_txn(
        session, upload.id, txn_date=date(2021, 2, 1), sequence=0,
        txn_type=TransactionType.REDEMPTION, units=Decimal("-100"), nav=Decimal("15"),
    )
    session.commit()
    service.rebuild_lots_for_folio(session, "F1")

    assert get_current_holdings(session) == []


def test_partial_redemption_shows_remaining_units_only(session: Session) -> None:
    upload = _make_upload(session)
    _add_txn(
        session, upload.id, txn_date=date(2021, 1, 1), sequence=0,
        units=Decimal("100"), nav=Decimal("10"),
    )
    _add_txn(
        session, upload.id, txn_date=date(2021, 2, 1), sequence=0,
        txn_type=TransactionType.REDEMPTION, units=Decimal("-40"), nav=Decimal("15"),
    )
    session.commit()
    service.rebuild_lots_for_folio(session, "F1")

    holdings = get_current_holdings(session)

    assert len(holdings) == 1
    assert holdings[0].units_remaining == Decimal("60")


def test_holdings_aggregate_multiple_lots_in_same_scheme(session: Session) -> None:
    upload = _make_upload(session)
    _add_txn(
        session, upload.id, txn_date=date(2021, 1, 1), sequence=0,
        units=Decimal("50"), nav=Decimal("10"),
    )
    _add_txn(
        session, upload.id, txn_date=date(2021, 2, 1), sequence=0,
        units=Decimal("50"), nav=Decimal("20"),
    )
    session.commit()
    service.rebuild_lots_for_folio(session, "F1")

    holdings = get_current_holdings(session)

    assert len(holdings) == 1
    h = holdings[0]
    assert h.units_remaining == Decimal("100")
    # blended average cost: (50*10 + 50*20) / 100 = 15
    assert h.average_cost_per_unit == Decimal("15")


def test_holdings_by_amc_aggregates_across_schemes(session: Session) -> None:
    upload = _make_upload(session)
    _add_txn(
        session, upload.id, amc="AMC One", scheme_name="Fund A", amfi_code="111",
        txn_date=date(2021, 1, 1), units=Decimal("100"), nav=Decimal("10"),
    )
    _add_txn(
        session, upload.id, amc="AMC One", scheme_name="Fund B", amfi_code="222",
        txn_date=date(2021, 1, 1), units=Decimal("50"), nav=Decimal("20"),
    )
    _add_txn(
        session, upload.id, amc="AMC Two", scheme_name="Fund C", amfi_code="333",
        txn_date=date(2021, 1, 1), units=Decimal("10"), nav=Decimal("100"),
    )
    session.commit()
    service.rebuild_lots_for_folio(session, "F1")

    by_amc = {row.amc: row for row in get_holdings_by_amc(session)}

    assert by_amc["AMC One"].scheme_count == 2
    assert by_amc["AMC One"].cost_basis_paise == 200_000  # 1000 + 1000 rupees
    assert by_amc["AMC Two"].scheme_count == 1
    assert by_amc["AMC Two"].cost_basis_paise == 100_000
