"""Zerodha Console "Tradebook" export, filtered to the MF segment --
real-data-verified structure (2026-08, `tradebook-<client-id>-MF.csv`,
downloaded from Console > Reports > Tradebook, segment "Mutual Fund"):

    symbol,isin,trade_date,exchange,segment,series,trade_type,auction,quantity,price,
        trade_id,order_id,order_execution_time
    MIRAE ASSET FLEXI CAP FUND - DIRECT PLAN,INF769K01JJ2,2025-08-01,BSE,MF,,buy,false,
        62.337000,16.041000,2004399965,2004399965,2025-08-01T00:00:00

A clean single-row header, unlike Dezerv's multi-line quoted-preamble
format -- no structural header-hunting needed.

**Carries ISIN directly** -- unlike Dezerv's "Security" name-only column,
this is an exact-match problem for `zerodha_matching.py`, not a fuzzy one.

**`symbol` is not stable per ISIN, real-data-verified**: the same ISIN
(`INF769K01JJ2`) appears in this format as both "MIRAE ASSET FLEXI CAP FUND
- DIRECT PLAN" and, later, "MIRAE ASSET FLEXI CAP FUND DIRECT PLAN -
GROWTH" -- callers must group/match by `isin`, never by `symbol` text.

**`quantity` and `price` are both given directly** -- unlike Dezerv (a
rupee amount only, units back-computed from NAV-on-date), this format
needs no NAV lookup at parse time; `amount_paise` is computed here via
`money.py::to_paise_strict(quantity * price)`.

**`trade_id` is a real, globally-unique per-row identifier** (real-data-
verified: no repeats across 172 real rows) -- a strictly better ordering
key than anything Dezerv's format offers, since Dezerv has no per-row ID
at all.

Only `trade_type` "buy"/"sell" (lowercase) are recognized; anything else is
collected into `unrecognized_trade_types` rather than guessed at, same
"flag, don't guess" discipline as Dezerv's Transaction Description
handling. `segment` is filtered to "MF" defensively -- this export format
supports other segments (Equity, F&O, Currency, Commodity) even though a
correctly-scoped MF download never mixes them in practice.
"""

import csv
import io
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation

from fundlens.money import to_paise_strict

_MF_SEGMENT = "MF"
_BUY = "buy"
_SELL = "sell"
_REQUIRED_COLUMNS = (
    "symbol",
    "isin",
    "trade_date",
    "segment",
    "trade_type",
    "quantity",
    "price",
    "trade_id",
)


class ZerodhaFormatError(Exception):
    """The file doesn't look like a Zerodha Tradebook export at all."""


@dataclass(frozen=True)
class ZerodhaRow:
    isin: str
    symbol: str
    trade_type: str  # "buy" or "sell"
    txn_date: date
    quantity: Decimal
    price: Decimal
    amount_paise: int  # always a positive magnitude; direction comes from trade_type
    trade_id: str


@dataclass(frozen=True)
class ZerodhaParseResult:
    rows: list[ZerodhaRow]
    unrecognized_trade_types: set[str]
    rows_missing_isin: int


def _parse_decimal(value: str) -> Decimal | None:
    cleaned = value.strip().replace(",", "")
    if not cleaned:
        return None
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        return None


def parse_zerodha_csv(content: str) -> ZerodhaParseResult:
    reader = csv.DictReader(io.StringIO(content))
    if reader.fieldnames is None:
        raise ZerodhaFormatError("Empty file")

    header = {name.strip() for name in reader.fieldnames}
    missing = [c for c in _REQUIRED_COLUMNS if c not in header]
    if missing:
        raise ZerodhaFormatError(
            f"Zerodha tradebook header is missing expected column(s): {missing}"
        )

    rows: list[ZerodhaRow] = []
    unrecognized: set[str] = set()
    rows_missing_isin = 0

    for raw_row in reader:
        if (raw_row.get("segment") or "").strip() != _MF_SEGMENT:
            continue

        trade_type = (raw_row.get("trade_type") or "").strip().lower()
        if trade_type not in (_BUY, _SELL):
            unrecognized.add(trade_type)
            continue

        isin = (raw_row.get("isin") or "").strip()
        if not isin:
            rows_missing_isin += 1
            continue

        trade_date_str = (raw_row.get("trade_date") or "").strip()
        try:
            txn_date = date.fromisoformat(trade_date_str)
        except ValueError:
            continue

        quantity = _parse_decimal(raw_row.get("quantity") or "")
        price = _parse_decimal(raw_row.get("price") or "")
        trade_id = (raw_row.get("trade_id") or "").strip()
        if quantity is None or price is None or not trade_id:
            continue

        rows.append(
            ZerodhaRow(
                isin=isin,
                symbol=(raw_row.get("symbol") or "").strip(),
                trade_type=trade_type,
                txn_date=txn_date,
                quantity=quantity,
                price=price,
                amount_paise=to_paise_strict(quantity * price),
                trade_id=trade_id,
            )
        )

    return ZerodhaParseResult(
        rows=rows,
        unrecognized_trade_types=unrecognized,
        rows_missing_isin=rows_missing_isin,
    )
