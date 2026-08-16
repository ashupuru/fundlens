"""Resolves a Dezerv `Security` name (e.g. "Mirae Asset Midcap Direct-G")
to a real, current AMFI-listed scheme — real-data-verified against the
live AMFI feed (2026-08) to handle the actual naming variety AMCs use:

- "360 ONE Focused Fund-Direct Plan-Growth" vs "...-Direct Plan-Dividend"
- "SBI OVERNIGHT FUND - DIRECT PLAN - GROWTH" vs "... IDCW" variants
- "ICICI Prudential Dividend Yield Equity Fund Direct Plan Growth Option"
  — "Dividend Yield" here is the fund's actual *category* name, not an
  IDCW/payout option, so a naive "exclude anything containing 'dividend'"
  filter would wrongly reject this valid growth-option scheme.
- "HDFC Nifty 50 Index Fund - Direct Plan" — growth-by-omission, no
  literal "Growth" in the name at all.
- "Mirae Asset Midcap Fund- Direct Growth Option" (the correct match for
  Dezerv's "Mirae Asset Midcap Direct-G") vs "Mirae Asset Large & Midcap
  Fund - Direct Plan - Growth" (a *different*, real fund whose name is a
  superset of the first's tokens) — this is why matching uses Jaccard
  similarity (penalizes a candidate's *extra* tokens too), not simple
  containment: containment alone would score both candidates equally
  since every Dezerv token appears in both names.

Auto-accepts only when exactly one candidate is the unambiguous top scorer
above a confidence floor; ties or low scores are reported as suggestions
for manual confirmation, never silently guessed (this is the "Auto-match
with a preview step" design chosen explicitly over blind automation).
"""

import re
from dataclasses import dataclass

from fundlens.market_data.records import SchemeMasterRow

_STOPWORDS = frozenset({"fund", "funds", "plan", "scheme", "schemes", "the", "of"})
_IDCW_SIGNALS = (
    "idcw",
    "dividend payout",
    "dividend reinvest",
    "reinvestment of income",
    "payout of income",
    "income distribution",
)
_CONFIDENCE_FLOOR = 0.5

# Plan/option/distribution vocabulary, stripped only for the token-similarity
# *score* (not for the growth/IDCW *filter*, which needs these words intact
# to tell a growth scheme from a dividend one). Real-data-verified this
# matters: without stripping these, "Mirae Asset Midcap Fund- Direct Growth
# Option" and the unrelated "Mirae Asset Large & Midcap Fund - Direct Plan -
# Growth" tied at the same score, because both names' extra plan-vocabulary
# tokens ("direct", "growth"/"plan") inflated their union equally — scoring
# only the fund-identity tokens (AMC + strategy name) breaks the tie
# correctly in favor of the exact match.
_SCORING_NOISE_WORDS = frozenset(
    {
        "direct",
        "growth",
        "option",
        "dividend",
        "idcw",
        "payout",
        "reinvestment",
        "reinvest",
        "income",
        "distribution",
        "cum",
        "capital",
        "withdrawal",
        "daily",
        "weekly",
        "monthly",
    }
)


def _tokens(name: str, *, for_scoring: bool = False) -> frozenset[str]:
    normalized = re.sub(r"[^a-z0-9]+", " ", name.lower())
    excluded = _STOPWORDS | _SCORING_NOISE_WORDS if for_scoring else _STOPWORDS
    return frozenset(t for t in normalized.split() if t and t not in excluded)


def _jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    if not a and not b:
        return 0.0
    union = a | b
    if not union:
        return 0.0
    return len(a & b) / len(union)


def _is_growth_candidate(scheme_name: str) -> bool:
    lowered = scheme_name.lower()
    if "direct" not in lowered:
        return False
    has_growth = "growth" in lowered
    # Bare "dividend" (an IDCW/payout option) is a negative signal, but
    # "dividend yield" is a real fund-category name (e.g. "ICICI Prudential
    # Dividend Yield Equity Fund") and must not be treated as one --
    # real-data-verified both directions live against the AMFI feed. Strip
    # "dividend yield" occurrences *before* checking for bare "dividend" --
    # a name can legitimately contain both the category name AND a separate
    # IDCW/payout-option "dividend" elsewhere (e.g. "...Dividend Yield Equity
    # Fund Direct Plan Dividend Option"), and a plain substring check on the
    # whole string would let that second, real IDCW signal hide behind the
    # first, unrelated match.
    has_bare_dividend = "dividend" in lowered.replace("dividend yield", "")
    has_idcw_signal = has_bare_dividend or any(signal in lowered for signal in _IDCW_SIGNALS)
    # Growth-by-omission (e.g. "HDFC Nifty 50 Index Fund - Direct Plan")
    # passes since it has no IDCW signal at all; a scheme with both an
    # IDCW-ish signal AND "growth" (the ICICI "Dividend Yield...Growth
    # Option" case) is still included -- "growth" wins when both appear.
    return has_growth or not has_idcw_signal


@dataclass(frozen=True)
class MatchCandidate:
    amfi_code: str
    scheme_name: str
    isin: str | None
    score: float


@dataclass(frozen=True)
class MatchResult:
    security_name: str
    matched: MatchCandidate | None
    reason: str | None  # set only when matched is None
    suggestions: list[MatchCandidate]  # top candidates, for manual review


def _strip_dezerv_suffix(security_name: str) -> tuple[str, bool]:
    """Returns (descriptor, is_growth). Only "-G" (Direct Growth) has been
    observed in real data; any other/missing suffix is flagged rather than
    guessed at, since there's no verified evidence for what it would mean.
    """
    stripped = security_name.strip()
    if stripped.lower().endswith("direct-g"):
        return stripped[: -len("Direct-G")].strip(" -"), True
    return stripped, False


def resolve_security(
    security_name: str, amfi_schemes: list[SchemeMasterRow]
) -> MatchResult:
    descriptor, is_growth = _strip_dezerv_suffix(security_name)
    if not is_growth:
        return MatchResult(
            security_name=security_name,
            matched=None,
            reason="unrecognized_plan_suffix",
            suggestions=[],
        )

    descriptor_tokens = _tokens(descriptor, for_scoring=True)
    candidates = [s for s in amfi_schemes if _is_growth_candidate(s.scheme_name)]

    scored = sorted(
        (
            MatchCandidate(
                amfi_code=s.amfi_code,
                scheme_name=s.scheme_name,
                isin=s.isin_growth,
                score=_jaccard(descriptor_tokens, _tokens(s.scheme_name, for_scoring=True)),
            )
            for s in candidates
        ),
        key=lambda c: c.score,
        reverse=True,
    )
    scored = [c for c in scored if c.score > 0][:10]

    if not scored:
        return MatchResult(
            security_name=security_name, matched=None, reason="no_candidates", suggestions=[]
        )

    top_score = scored[0].score
    if top_score < _CONFIDENCE_FLOOR:
        return MatchResult(
            security_name=security_name,
            matched=None,
            reason="low_confidence",
            suggestions=scored[:5],
        )

    tied = [c for c in scored if c.score == top_score]
    if len(tied) > 1:
        return MatchResult(
            security_name=security_name, matched=None, reason="ambiguous", suggestions=tied
        )

    return MatchResult(
        security_name=security_name, matched=scored[0], reason=None, suggestions=scored[:5]
    )
