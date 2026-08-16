"""Thin wrapper around casparser.read_cas_pdf.

FundLens P0 targets CAMS/KFintech detailed CAS statements only (PRD FR-1.1).
NSDL/CDSL demat-account CAS statements are a different document shape and are
explicitly out of scope for now — rejected clearly rather than mishandled.
"""

from pathlib import Path

from casparser import read_cas_pdf
from casparser.types import CASData, NSDLCASData


class UnsupportedCASTypeError(Exception):
    """Raised when the PDF is a CAS type FundLens doesn't yet support."""


def parse_cas_pdf(path: Path, password: str) -> CASData:
    result = read_cas_pdf(str(path), password)
    if isinstance(result, NSDLCASData):
        raise UnsupportedCASTypeError(
            "NSDL/CDSL CAS statements are not yet supported; FundLens P0 targets "
            "CAMS/KFintech detailed CAS statements only (PRD FR-1.1)."
        )
    return result
