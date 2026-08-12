"""Canonical scheme identifier: prefer ISIN, then AMFI code, then raw name.

Used everywhere a group of transactions needs to be scoped to "one scheme" —
natural-key computation (ingestion) and FIFO lot grouping (compute) both
delegate here, so a transaction's dedupe grouping and its lot grouping can
never disagree about what counts as the same scheme.
"""


def scheme_key(isin: str | None, amfi_code: str | None, scheme_name: str) -> str:
    return isin or amfi_code or scheme_name
