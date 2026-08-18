"""Resolves a Zerodha tradebook row's `isin` to a real, current AMFI-listed
scheme. Unlike `dezerv_matching.py` (which only has a scheme-name string to
work with and needs fuzzy Jaccard scoring), Zerodha's export carries ISIN
directly -- this is an exact-match lookup against AMFI's own
`isin_growth`/`isin_div_reinvestment` fields
(`market_data/records.py::SchemeMasterRow`), not a similarity-scored guess.

A given ISIN is expected to identify exactly one scheme. The defensive case
of one ISIN appearing on more than one AMFI scheme row (not observed in
real data, but AMFI's feed isn't a database with a uniqueness constraint)
is flagged as `ambiguous` rather than silently picking the first match --
same "flag, don't guess" discipline as everywhere else in this app.
"""

from dataclasses import dataclass

from fundlens.market_data.records import SchemeMasterRow


@dataclass(frozen=True)
class MatchResult:
    isin: str
    matched: SchemeMasterRow | None
    reason: str | None  # set only when matched is None


def _isin_index(amfi_schemes: list[SchemeMasterRow]) -> dict[str, list[SchemeMasterRow]]:
    index: dict[str, list[SchemeMasterRow]] = {}
    for scheme in amfi_schemes:
        for isin in (scheme.isin_growth, scheme.isin_div_reinvestment):
            if isin:
                index.setdefault(isin, []).append(scheme)
    return index


def resolve_isin(isin: str, amfi_schemes: list[SchemeMasterRow]) -> MatchResult:
    index = _isin_index(amfi_schemes)
    candidates = index.get(isin, [])

    if not candidates:
        return MatchResult(isin=isin, matched=None, reason="no_candidates")

    distinct_codes = {c.amfi_code for c in candidates}
    if len(distinct_codes) > 1:
        return MatchResult(isin=isin, matched=None, reason="ambiguous")

    return MatchResult(isin=isin, matched=candidates[0], reason=None)
