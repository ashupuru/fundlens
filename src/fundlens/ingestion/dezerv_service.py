"""Orchestrates a Dezerv BANK BOOK import in two steps, matching the
"auto-match with a preview step" design chosen for this format's scheme-
resolution risk (dezerv_matching.py's docstring has the real-data cases
that make blind auto-matching unsafe):

1. `preview_dezerv_csv` — parses the file and proposes a scheme mapping,
   writing nothing to the database. The caller (API/UI) shows this to the
   user for approval or correction.
2. `commit_dezerv_csv` — takes the same file plus a *confirmed* mapping
   (security name -> amfi_code, or None to skip that scheme's rows
   entirely) and actually ingests: backfills NAV history for every
   resolved scheme via mfapi.in (same mechanism P0.2 uses for any other
   scheme), back-computes units from each transaction's rupee amount and
   that scheme's NAV on the transaction date (nearest available trading
   day on/before it — this format has no units column at all), and merges
   through the same idempotent natural-key path every other ingestion
   route uses.

A scheme resolvable in the mapping but whose NAV backfill fails (network
error) or has no NAV on/before a given transaction's date degrades that
row to skipped-and-reported, never a fabricated unit count.

**Cross-source duplicate detection.** Real-data-verified against a live
portfolio: the same real transaction can already exist in `raw_transaction`
from a CAS/CSV import *before* a Dezerv import of the same activity is run
-- Dezerv reports the demat/custodian account as `folio`, while a CAS
folio is the AMC's own folio number, so the natural-key dedup (keyed on
`folio` among other fields) never recognizes these as the same
transaction and both get inserted. Verified case: 35 transactions,
each appearing once from CAS (`parser_version` from casparser) and once
from Dezerv, identical scheme/date/type/amount but different `folio` --
and units matched to 2 decimal places in 34/35 of them despite being
independently derived (exact from CAS, back-computed from NAV for
Dezerv), confirming these are true duplicates rather than coincidental
collisions. `commit_dezerv_csv` therefore checks, per row, whether a
transaction already exists for the same (amfi_code, txn_date, amount,
buy/sell-equivalent txn_type) *regardless of folio*, and skips inserting
it if so -- reported distinctly via `cross_source_duplicate_count` /
`cross_source_duplicates`, never silently dropped. Units are
deliberately *not* part of this match (Dezerv's are back-computed and
can legitimately differ slightly from CAS's exact figure); amount_paise
is used instead since it's stored exactly by both sources.
"""

import hashlib
import json
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy.orm import Session

from fundlens.db.models import RawCASUpload, TransactionType, UploadStatus
from fundlens.ingestion.broker_import_shared import (
    existing_cross_source_transaction,
    resolve_amc_names,
)
from fundlens.ingestion.dezerv_csv import DezervRow, parse_dezerv_csv
from fundlens.ingestion.dezerv_matching import MatchCandidate, resolve_security
from fundlens.ingestion.merge import merge_transactions
from fundlens.ingestion.natural_key import compute_natural_key
from fundlens.ingestion.records import TransactionRecord
from fundlens.ingestion.service import find_existing_parsed_upload
from fundlens.market_data.amfi_client import fetch_navall_text, parse_navall_scheme_master
from fundlens.tax.grandfathering import fmv_per_unit_on_or_before as _nav_on_or_before

PARSER_VERSION = "dezerv-csv-v1"


@dataclass(frozen=True)
class DezervSchemePreview:
    security_name: str
    symbol_code: str
    txn_count: int
    matched: MatchCandidate | None
    reason: str | None
    suggestions: list[MatchCandidate]


@dataclass(frozen=True)
class DezervPreview:
    schemes: list[DezervSchemePreview]
    unrecognized_transaction_descriptions: list[str]
    total_mfdemat_rows: int
    date_range: tuple[date, date] | None


def preview_dezerv_csv(content: str) -> DezervPreview:
    parsed = parse_dezerv_csv(content)

    amfi_text = fetch_navall_text()
    schemes_master = parse_navall_scheme_master(amfi_text)

    by_security: dict[str, list[DezervRow]] = {}
    for row in parsed.rows:
        by_security.setdefault(row.security, []).append(row)

    scheme_previews = [
        DezervSchemePreview(
            security_name=security_name,
            symbol_code=rows[0].symbol_code,
            txn_count=len(rows),
            matched=(result := resolve_security(security_name, schemes_master)).matched,
            reason=result.reason,
            suggestions=result.suggestions,
        )
        for security_name, rows in by_security.items()
    ]

    dates = [r.txn_date for r in parsed.rows]
    return DezervPreview(
        schemes=scheme_previews,
        unrecognized_transaction_descriptions=sorted(parsed.unrecognized_transaction_descriptions),
        total_mfdemat_rows=len(parsed.rows),
        date_range=(min(dates), max(dates)) if dates else None,
    )


@dataclass(frozen=True)
class DezervCommitResult:
    upload_id: str
    inserted_count: int
    duplicate_count: int
    skipped_count: int
    skipped_reasons: dict[str, int]  # security_name -> row count skipped for it
    cross_source_duplicate_count: int
    cross_source_duplicates: dict[str, int]  # security_name -> row count matched to an existing txn


def commit_dezerv_csv(
    session: Session,
    content: str,
    mapping: dict[str, str | None],
    source_filename: str | None = None,
) -> DezervCommitResult:
    hash_input = content + json.dumps(mapping, sort_keys=True)
    file_hash = hashlib.sha256(hash_input.encode("utf-8")).hexdigest()

    existing = find_existing_parsed_upload(session, file_hash)
    if existing is not None:
        return DezervCommitResult(
            upload_id=existing.id, inserted_count=0, duplicate_count=0, skipped_count=0,
            skipped_reasons={}, cross_source_duplicate_count=0, cross_source_duplicates={},
        )

    parsed = parse_dezerv_csv(content)

    amfi_text = fetch_navall_text()
    schemes_master = {s.amfi_code: s for s in parse_navall_scheme_master(amfi_text)}

    resolved_codes = {code for code in mapping.values() if code}
    valid_codes = resolved_codes & set(schemes_master)
    amc_by_code = resolve_amc_names(session, valid_codes)

    skipped_reasons: dict[str, int] = {}
    cross_source_duplicates: dict[str, int] = {}
    records: list[TransactionRecord] = []
    ordinal_counts: dict[tuple, int] = {}
    sequence_counts: dict[tuple[str, str], int] = {}

    for row in sorted(parsed.rows, key=lambda r: (r.security, r.txn_date)):
        amfi_code = mapping.get(row.security)
        scheme = schemes_master.get(amfi_code) if amfi_code else None
        if scheme is None:
            skipped_reasons[row.security] = skipped_reasons.get(row.security, 0) + 1
            continue

        is_buy = row.transaction_description == "Buy"
        signed_amount_paise = row.amount_paise if is_buy else -row.amount_paise

        if existing_cross_source_transaction(
            session, scheme.amfi_code, row.txn_date, is_buy, signed_amount_paise
        ):
            cross_source_duplicates[row.security] = cross_source_duplicates.get(row.security, 0) + 1
            continue

        nav = _nav_on_or_before(session, scheme.amfi_code, row.txn_date)
        if nav is None or nav == 0:
            skipped_reasons[row.security] = skipped_reasons.get(row.security, 0) + 1
            continue

        units = (Decimal(row.amount_paise) / 100) / nav
        txn_type = TransactionType.PURCHASE if is_buy else TransactionType.REDEMPTION
        signed_units = units if is_buy else -units

        scheme_identifier = scheme.isin_growth or scheme.amfi_code
        tie_key = (
            row.custodian_account,
            scheme_identifier,
            row.txn_date,
            txn_type.value,
            signed_amount_paise,
            signed_units,
            None,
        )
        ordinal = ordinal_counts.get(tie_key, 0)
        ordinal_counts[tie_key] = ordinal + 1

        scheme_group = (row.custodian_account, scheme_identifier)
        sequence = sequence_counts.get(scheme_group, 0)
        sequence_counts[scheme_group] = sequence + 1

        records.append(
            TransactionRecord(
                pan=None,
                folio=row.custodian_account,
                amc=amc_by_code.get(scheme.amfi_code, "Unknown AMC"),
                scheme_name=scheme.scheme_name,
                isin=scheme.isin_growth,
                amfi_code=scheme.amfi_code,
                txn_type=txn_type,
                txn_date=row.txn_date,
                sequence=sequence,
                description=f"Dezerv: {row.transaction_description}",
                units=signed_units,
                nav=nav,
                amount_paise=signed_amount_paise,
                balance_after=None,
                dividend_rate=None,
                gift_folio=None,
                natural_key=compute_natural_key(
                    folio=row.custodian_account,
                    scheme_identifier=scheme_identifier,
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

    return DezervCommitResult(
        upload_id=upload.id,
        inserted_count=merge_result.inserted_count,
        duplicate_count=merge_result.duplicate_count,
        skipped_count=sum(skipped_reasons.values()),
        skipped_reasons=skipped_reasons,
        cross_source_duplicate_count=sum(cross_source_duplicates.values()),
        cross_source_duplicates=cross_source_duplicates,
    )
