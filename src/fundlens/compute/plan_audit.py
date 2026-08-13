"""Direct-vs-Regular audit (PRD §5.3, compute-engine.md §6): flags
regular-plan holdings by parsing the scheme name — AMFI scheme names
reliably embed "Direct Plan"/"Regular Plan" (real-data-verified against
the live AMFI feed, e.g. "UTI Nifty 50 Index Fund - Growth Option- Direct").

**Narrower than originally planned**: the original design computed
commission drag in ₹/year from the expense-ratio delta between the held
regular plan and its direct-plan twin. No free expense-ratio data source
exists — live-checked during P1.1/P1.3: mfapi.in's scheme metadata has no
TER field, and AMFI's TER disclosures are scattered per-AMC filings, not a
unified free feed — so this ships as a binary flag only, not a rupee
estimate, per the explicit P1.3 scope decision (binary cost signal instead
of a ₹/year figure).
"""

import enum


class PlanType(enum.StrEnum):
    DIRECT = "direct"
    REGULAR = "regular"
    # Genuinely ambiguous (e.g. ETFs trade on-exchange and have no
    # Direct/Regular distinction at all) or the scheme name doesn't say —
    # never guessed either way.
    UNKNOWN = "unknown"


def detect_plan_type(scheme_name: str) -> PlanType:
    normalized = scheme_name.strip().lower()
    if "direct" in normalized:
        return PlanType.DIRECT
    if "regular" in normalized:
        return PlanType.REGULAR
    return PlanType.UNKNOWN
