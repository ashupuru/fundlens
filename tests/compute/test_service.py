import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from fundlens.compute import service
from fundlens.db.models import (
    Lot,
    RawCASUpload,
    RawTransaction,
    RealizedGain,
    TransactionType,
    UploadStatus,
)


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
    scheme_name: str = "Fund A",
    amfi_code: str = "111",
    txn_type: TransactionType = TransactionType.PURCHASE,
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
            amc="Test AMC",
            scheme_name=scheme_name,
            amfi_code=amfi_code,
            txn_type=txn_type,
            txn_date=txn_date,
            sequence=sequence,
            units=units,
            nav=nav,
            amount_paise=amount_paise,
            natural_key=f"key-{uuid.uuid4().hex}",
        )
    )


def test_rebuild_persists_lots_for_a_folio(session: Session) -> None:
    upload = _make_upload(session)
    _add_txn(
        session, upload.id, txn_date=date(2021, 1, 1), units=Decimal("100"), nav=Decimal("10")
    )
    session.commit()

    summary = service.rebuild_lots_for_folio(session, "F1")

    assert summary.schemes_processed == 1
    assert summary.lots_created == 1
    assert summary.errors == []

    lots = session.scalars(select(Lot).where(Lot.folio == "F1")).all()
    assert len(lots) == 1
    assert lots[0].units_remaining == Decimal("100")
    assert lots[0].cost_per_unit == Decimal("10")


def test_rebuild_persists_realized_gains_on_redemption(session: Session) -> None:
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

    summary = service.rebuild_lots_for_folio(session, "F1")

    assert summary.lots_created == 1
    assert summary.realized_gains_created == 1
    gains = session.scalars(select(RealizedGain).where(RealizedGain.folio == "F1")).all()
    assert len(gains) == 1
    assert gains[0].units_consumed == Decimal("40")
    assert gains[0].realized_gain_paise == 20_000  # (15-10)*40 = 200 rupees


def test_rebuild_is_idempotent_not_additive(session: Session) -> None:
    upload = _make_upload(session)
    _add_txn(
        session, upload.id, txn_date=date(2021, 1, 1), units=Decimal("100"), nav=Decimal("10")
    )
    session.commit()

    service.rebuild_lots_for_folio(session, "F1")
    service.rebuild_lots_for_folio(session, "F1")  # re-run without new transactions

    lots = session.scalars(select(Lot).where(Lot.folio == "F1")).all()
    assert len(lots) == 1  # not 2 — old lots were cleared, not accumulated


def test_rebuild_groups_by_scheme_independently(session: Session) -> None:
    upload = _make_upload(session)
    _add_txn(
        session, upload.id, scheme_name="Fund A", amfi_code="111",
        txn_date=date(2021, 1, 1), units=Decimal("100"), nav=Decimal("10"),
    )
    _add_txn(
        session, upload.id, scheme_name="Fund B", amfi_code="222",
        txn_date=date(2021, 1, 1), units=Decimal("50"), nav=Decimal("20"),
    )
    session.commit()

    summary = service.rebuild_lots_for_folio(session, "F1")

    assert summary.schemes_processed == 2
    assert summary.lots_created == 2
    scheme_keys = {lot.scheme_key for lot in session.scalars(select(Lot)).all()}
    assert scheme_keys == {"111", "222"}


def test_insufficient_lots_gap_filled_not_dropped(session: Session) -> None:
    """A redemption exceeding recorded history (e.g. a Dezerv import whose
    date window starts mid-holding) used to discard the whole scheme's
    lots, silently dropping it from holdings. It should instead get a
    synthetic, zero-cost, provenance-less "gap-fill" lot sized to exactly
    the shortfall -- the fund still shows up, just flagged."""
    upload = _make_upload(session)
    _add_txn(
        session, upload.id, txn_date=date(2021, 1, 1), units=Decimal("50"), nav=Decimal("10")
    )
    _add_txn(
        session, upload.id, txn_date=date(2021, 2, 1),
        txn_type=TransactionType.REDEMPTION, units=Decimal("-80"), nav=Decimal("15"),
    )
    session.commit()

    summary = service.rebuild_lots_for_folio(session, "F1")

    assert summary.errors == []
    assert summary.lots_created == 2  # the real 50-unit lot + the gap-fill lot
    assert len(summary.gap_filled) == 1
    assert summary.gap_filled[0].folio == "F1"
    assert summary.gap_filled[0].units_assumed == Decimal("30")  # 80 - 50 shortfall

    lots = session.scalars(select(Lot)).all()
    gap_lot = next(lot for lot in lots if lot.source_txn_id is None)
    assert gap_lot.zero_cost_flag is True
    assert gap_lot.units_remaining == Decimal("0")  # fully consumed by the redemption


def test_gap_fill_in_one_scheme_does_not_block_others(session: Session) -> None:
    upload = _make_upload(session)
    # Fund A: oversold relative to recorded history -- gets a gap-fill lot.
    _add_txn(
        session, upload.id, scheme_name="Fund A", amfi_code="111",
        txn_date=date(2021, 1, 1), units=Decimal("10"), nav=Decimal("10"),
    )
    _add_txn(
        session, upload.id, scheme_name="Fund A", amfi_code="111",
        txn_date=date(2021, 2, 1),
        txn_type=TransactionType.REDEMPTION, units=Decimal("-50"), nav=Decimal("15"),
    )
    # Fund B: clean purchase, should still succeed
    _add_txn(
        session, upload.id, scheme_name="Fund B", amfi_code="222",
        txn_date=date(2021, 1, 1), units=Decimal("100"), nav=Decimal("20"),
    )
    session.commit()

    summary = service.rebuild_lots_for_folio(session, "F1")

    assert summary.errors == []
    assert len(summary.gap_filled) == 1
    assert summary.gap_filled[0].scheme_key == "111"
    assert summary.lots_created == 3  # Fund A's real lot + its gap-fill lot + Fund B's lot
    lots = session.scalars(select(Lot)).all()
    assert {lot.scheme_key for lot in lots} == {"111", "222"}


def test_get_all_folios_returns_distinct_folios(session: Session) -> None:
    upload = _make_upload(session)
    _add_txn(session, upload.id, folio="F1", txn_date=date(2021, 1, 1), units=Decimal("10"))
    _add_txn(session, upload.id, folio="F1", txn_date=date(2021, 2, 1), units=Decimal("10"))
    _add_txn(session, upload.id, folio="F2", txn_date=date(2021, 1, 1), units=Decimal("10"))
    session.commit()

    assert service.get_all_folios(session) == {"F1", "F2"}


def test_get_all_folios_includes_folios_with_only_stale_derived_state(
    session: Session,
) -> None:
    """A folio whose transactions were all removed (e.g. deleting a PAN's
    data by hand) still has stale Lot/RealizedGain rows until a rebuild
    reaches it -- but rebuild_all_lots only ever calls
    rebuild_lots_for_folio for folios get_all_folios returns. Missing this
    case would leave orphaned lots forever, since nothing else ever
    revisits a transaction-less folio."""
    session.add(
        Lot(
            folio="GHOST",
            scheme_key="K1",
            open_date=date(2021, 1, 1),
            units_remaining=Decimal("10"),
            cost_per_unit=Decimal("100"),
            source_txn_id=None,
            source_txn_type="PURCHASE",
        )
    )
    session.commit()

    assert "GHOST" in service.get_all_folios(session)


def test_rebuild_all_lots_clears_orphaned_lots_for_a_deleted_folio(
    session: Session,
) -> None:
    session.add(
        Lot(
            folio="GHOST",
            scheme_key="K1",
            open_date=date(2021, 1, 1),
            units_remaining=Decimal("10"),
            cost_per_unit=Decimal("100"),
            source_txn_id=None,
            source_txn_type="PURCHASE",
        )
    )
    session.commit()

    service.rebuild_all_lots(session)

    assert session.scalars(select(Lot).where(Lot.folio == "GHOST")).all() == []


def test_rebuild_all_lots_processes_every_folio(session: Session) -> None:
    upload = _make_upload(session)
    _add_txn(
        session, upload.id, folio="F1", txn_date=date(2021, 1, 1),
        units=Decimal("10"), nav=Decimal("10"),
    )
    _add_txn(
        session, upload.id, folio="F2", txn_date=date(2021, 1, 1),
        units=Decimal("20"), nav=Decimal("20"),
    )
    session.commit()

    summary = service.rebuild_all_lots(session)

    assert summary.folios_processed == 2
    assert summary.lots_created == 2
