"""Idempotent merge of parsed transactions into raw_transaction (FR-1.2).

Re-uploading an overlapping CAS (or CSV) must never double-count: every
incoming record's natural_key is checked against both what's already in the
DB and what's already been staged earlier in this same batch.
"""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from fundlens.db.models import RawTransaction
from fundlens.ingestion.records import TransactionRecord


@dataclass(frozen=True)
class MergeResult:
    inserted_count: int
    duplicate_count: int


def merge_transactions(
    session: Session, upload_id: str, records: list[TransactionRecord]
) -> MergeResult:
    if not records:
        return MergeResult(inserted_count=0, duplicate_count=0)

    incoming_keys = [r.natural_key for r in records]
    existing_keys = set(
        session.scalars(
            select(RawTransaction.natural_key).where(
                RawTransaction.natural_key.in_(incoming_keys)
            )
        )
    )

    seen_keys = set(existing_keys)
    inserted = 0
    for record in records:
        if record.natural_key in seen_keys:
            continue
        session.add(
            RawTransaction(
                upload_id=upload_id,
                pan=record.pan,
                folio=record.folio,
                amc=record.amc,
                scheme_name=record.scheme_name,
                isin=record.isin,
                amfi_code=record.amfi_code,
                txn_type=record.txn_type,
                txn_date=record.txn_date,
                sequence=record.sequence,
                description=record.description,
                units=record.units,
                nav=record.nav,
                amount_paise=record.amount_paise,
                balance_after=record.balance_after,
                dividend_rate=record.dividend_rate,
                gift_folio=record.gift_folio,
                natural_key=record.natural_key,
            )
        )
        seen_keys.add(record.natural_key)
        inserted += 1

    return MergeResult(inserted_count=inserted, duplicate_count=len(records) - inserted)
