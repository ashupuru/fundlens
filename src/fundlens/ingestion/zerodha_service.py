"""Orchestrates a Zerodha Tradebook (MF segment) import in the same
two-step "auto-match with a preview step" shape as `dezerv_service.py`,
simplified where the format itself is simpler:

1. `preview_zerodha_csv` -- parses the file and proposes a scheme mapping
   *by ISIN* (not by the `symbol` text, which real data shows is unstable
   for the same fund over time -- see `zerodha_csv.py`'s docstring),
   writing nothing to the database.
2. `commit_zerodha_csv` -- takes the same file plus a confirmed mapping
   (isin -> amfi_code, or None to skip that scheme's rows entirely) and
   ingests. Unlike Dezerv, this format gives `quantity` and `price`
   directly, so there's no NAV-backfill/back-computation step at all --
   `amount_paise` and `units` come straight from the parsed row, and `nav`
   on the resulting `TransactionRecord` is simply that row's own `price`
   (the transaction's real per-unit price), the same convention the CAS
   and CSV-escape-hatch import paths already use.

**No folio/account column in this format at all** (unlike Dezerv's
`Custodian Account`) -- a Zerodha tradebook download is inherently scoped
to one account per file. Every row from this importer is recorded under a
constant synthetic folio, `"ZERODHA"` (documented judgment call, not a
guarantee of multi-account safety -- a user with more than one Zerodha
account would have both land under the same folio).

Cross-source duplicate detection and AMC-name resolution are shared with
Dezerv via `broker_import_shared.py` (`existing_cross_source_transaction`,
`resolve_amc_names`) -- same rationale (a broker's own account label is
never the same string as a CAS folio, so natural-key dedup alone can't
catch the same real transaction imported from both sources).
"""

import hashlib
import json
from dataclasses import dataclass
from datetime import date

from sqlalchemy.orm import Session

from fundlens.db.models import RawCASUpload, TransactionType, UploadStatus
from fundlens.ingestion.broker_import_shared import (
    existing_cross_source_transaction,
    resolve_amc_names,
)
from fundlens.ingestion.merge import merge_transactions
from fundlens.ingestion.natural_key import compute_natural_key
from fundlens.ingestion.records import TransactionRecord
from fundlens.ingestion.service import find_existing_parsed_upload
from fundlens.ingestion.zerodha_csv import ZerodhaRow, parse_zerodha_csv
from fundlens.ingestion.zerodha_matching import resolve_isin
from fundlens.market_data.amfi_client import fetch_navall_text, parse_navall_scheme_master
from fundlens.market_data.records import SchemeMasterRow

PARSER_VERSION = "zerodha-tradebook-csv-v1"
_FOLIO = "ZERODHA"


@dataclass(frozen=True)
class ZerodhaSchemePreview:
    isin: str
    symbol: str  # most recently observed symbol text for this ISIN, for display only
    txn_count: int
    matched_amfi_code: str | None
    matched_scheme_name: str | None
    reason: str | None


@dataclass(frozen=True)
class ZerodhaPreview:
    schemes: list[ZerodhaSchemePreview]
    unrecognized_trade_types: list[str]
    rows_missing_isin: int
    total_mf_rows: int
    date_range: tuple[date, date] | None


def preview_zerodha_csv(content: str) -> ZerodhaPreview:
    parsed = parse_zerodha_csv(content)

    amfi_text = fetch_navall_text()
    schemes_master = parse_navall_scheme_master(amfi_text)

    by_isin: dict[str, list[ZerodhaRow]] = {}
    for row in parsed.rows:
        by_isin.setdefault(row.isin, []).append(row)

    scheme_previews = []
    for isin, rows in by_isin.items():
        result = resolve_isin(isin, schemes_master)
        scheme_previews.append(
            ZerodhaSchemePreview(
                isin=isin,
                symbol=rows[-1].symbol,
                txn_count=len(rows),
                matched_amfi_code=result.matched.amfi_code if result.matched else None,
                matched_scheme_name=result.matched.scheme_name if result.matched else None,
                reason=result.reason,
            )
        )

    dates = [r.txn_date for r in parsed.rows]
    return ZerodhaPreview(
        schemes=scheme_previews,
        unrecognized_trade_types=sorted(parsed.unrecognized_trade_types),
        rows_missing_isin=parsed.rows_missing_isin,
        total_mf_rows=len(parsed.rows),
        date_range=(min(dates), max(dates)) if dates else None,
    )


@dataclass(frozen=True)
class ZerodhaCommitResult:
    upload_id: str
    inserted_count: int
    duplicate_count: int
    skipped_count: int
    skipped_reasons: dict[str, int]  # isin -> row count skipped for it
    cross_source_duplicate_count: int
    cross_source_duplicates: dict[str, int]  # isin -> row count matched to an existing txn


def commit_zerodha_csv(
    session: Session,
    content: str,
    mapping: dict[str, str | None],
    source_filename: str | None = None,
) -> ZerodhaCommitResult:
    hash_input = content + json.dumps(mapping, sort_keys=True)
    file_hash = hashlib.sha256(hash_input.encode("utf-8")).hexdigest()

    existing = find_existing_parsed_upload(session, file_hash)
    if existing is not None:
        return ZerodhaCommitResult(
            upload_id=existing.id, inserted_count=0, duplicate_count=0, skipped_count=0,
            skipped_reasons={}, cross_source_duplicate_count=0, cross_source_duplicates={},
        )

    parsed = parse_zerodha_csv(content)

    amfi_text = fetch_navall_text()
    schemes_master: dict[str, SchemeMasterRow] = {
        s.amfi_code: s for s in parse_navall_scheme_master(amfi_text)
    }

    resolved_codes = {code for code in mapping.values() if code}
    valid_codes = resolved_codes & set(schemes_master)
    amc_by_code = resolve_amc_names(session, valid_codes)

    skipped_reasons: dict[str, int] = {}
    cross_source_duplicates: dict[str, int] = {}
    records: list[TransactionRecord] = []
    ordinal_counts: dict[tuple, int] = {}
    sequence_counts: dict[str, int] = {}

    for row in sorted(parsed.rows, key=lambda r: (r.isin, r.txn_date, r.trade_id)):
        amfi_code = mapping.get(row.isin)
        scheme = schemes_master.get(amfi_code) if amfi_code else None
        if scheme is None:
            skipped_reasons[row.isin] = skipped_reasons.get(row.isin, 0) + 1
            continue

        is_buy = row.trade_type == "buy"
        signed_amount_paise = row.amount_paise if is_buy else -row.amount_paise

        if existing_cross_source_transaction(
            session,
            scheme.amfi_code,
            row.txn_date,
            is_buy,
            signed_amount_paise,
            units=row.quantity,
        ):
            cross_source_duplicates[row.isin] = cross_source_duplicates.get(row.isin, 0) + 1
            continue

        txn_type = TransactionType.PURCHASE if is_buy else TransactionType.REDEMPTION
        signed_units = row.quantity if is_buy else -row.quantity

        tie_key = (
            _FOLIO,
            row.isin,
            row.txn_date,
            txn_type.value,
            signed_amount_paise,
            signed_units,
            None,
        )
        ordinal = ordinal_counts.get(tie_key, 0)
        ordinal_counts[tie_key] = ordinal + 1

        sequence = sequence_counts.get(row.isin, 0)
        sequence_counts[row.isin] = sequence + 1

        records.append(
            TransactionRecord(
                pan=None,
                folio=_FOLIO,
                amc=amc_by_code.get(scheme.amfi_code, "Unknown AMC"),
                scheme_name=scheme.scheme_name,
                isin=row.isin,
                amfi_code=scheme.amfi_code,
                txn_type=txn_type,
                txn_date=row.txn_date,
                sequence=sequence,
                description=f"Zerodha: {row.trade_type}",
                units=signed_units,
                nav=row.price,
                amount_paise=signed_amount_paise,
                balance_after=None,
                dividend_rate=None,
                gift_folio=None,
                natural_key=compute_natural_key(
                    folio=_FOLIO,
                    scheme_identifier=row.isin,
                    txn_date=row.txn_date,
                    txn_type=txn_type.value,
                    amount_paise=signed_amount_paise,
                    units=signed_units,
                    balance_after=None,
                    ordinal=ordinal,
                ),
            )
        )

    upload = RawCASUpload(
        file_hash=file_hash,
        source_filename=source_filename,
        parser_version=PARSER_VERSION,
        status=UploadStatus.PARSED,
    )
    session.add(upload)
    session.flush()

    merge_result = merge_transactions(session, upload.id, records)
    session.commit()

    return ZerodhaCommitResult(
        upload_id=upload.id,
        inserted_count=merge_result.inserted_count,
        duplicate_count=merge_result.duplicate_count,
        skipped_count=sum(skipped_reasons.values()),
        skipped_reasons=skipped_reasons,
        cross_source_duplicate_count=sum(cross_source_duplicates.values()),
        cross_source_duplicates=cross_source_duplicates,
    )
