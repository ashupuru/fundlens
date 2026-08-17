"""Determinism validator (docs/architecture/ai-layer.md §2): plain Python,
no LLM involved. This is the concrete enforcement of the AI layer's single
governing rule -- the LLM interprets, it never calculates. Every grounded
statement's cited metric paths must resolve in the snapshot AND the
resolved value must actually appear in the statement's text; on top of
that, the entire text is scanned for any number-shaped token that doesn't
match *something* in the snapshot at all, catching numbers introduced
without any citation. Either failure drops the statement from the
rendered narrative and logs why -- never silently "corrected".

Only `NarrativeResponse.statements` (numeric claims) go through this.
`news_context` items are deliberately never validated here: they cite the
web via the `web_search` tool, not the snapshot, and the doc's own design
is that this content type is *allowed* to reference the outside world.
"""

import re
from dataclasses import dataclass

from fundlens.ai.claude_client import GroundedStatement, NarrativeResponse
from fundlens.ai.snapshot import PortfolioMetricsSnapshot

_NUMBER_RE = re.compile(r"-?\d+\.?\d*")
_PATH_SEGMENT_RE = re.compile(r"([^.\[\]]+)|\[(\d+)\]")

# Absolute tolerance for matching a snapshot value against a number-token in
# text -- accounts for Claude rounding "68.5714%" to "68.6%" or "69%".
_MATCH_TOLERANCE = 0.05


@dataclass(frozen=True)
class RejectedStatement:
    statement: GroundedStatement
    reason: str


@dataclass(frozen=True)
class ValidatedNarrative:
    accepted: list[GroundedStatement]
    rejected: list[RejectedStatement]


def resolve_path(snapshot: PortfolioMetricsSnapshot, path: str) -> object | None:
    """Resolves a dotted/bracket path like "allocation.equity_drift_pct" or
    "funds[2].expense_ratio" against the snapshot. Never raises -- any
    unresolvable path (typo'd field, out-of-range index) returns None,
    which the caller treats as a validation failure, not an error."""
    current: object = snapshot.model_dump(mode="json")
    for match in _PATH_SEGMENT_RE.finditer(path):
        key, index = match.group(1), match.group(2)
        if key is not None:
            if not isinstance(current, dict) or key not in current:
                return None
            current = current[key]
        else:
            if index is None or not isinstance(current, list):
                return None
            idx = int(index)
            if idx < 0 or idx >= len(current):
                return None
            current = current[idx]
    return current


def _value_matches_text(value: object, text: str) -> bool:
    if isinstance(value, bool):
        return str(value).lower() in text.lower()
    if isinstance(value, int | float):
        return _number_matches_text(float(value), text)
    if isinstance(value, str):
        return value in text
    return False


def _number_matches_text(value: float, text: str, tolerance: float = _MATCH_TOLERANCE) -> bool:
    for token in _NUMBER_RE.findall(text):
        try:
            candidate = float(token)
        except ValueError:
            continue
        if abs(candidate - value) <= tolerance:
            return True
    return False


def validate_statement(
    statement: GroundedStatement, snapshot: PortfolioMetricsSnapshot
) -> tuple[bool, str | None]:
    if not statement.cited_metric_paths:
        return False, "statement cites no metric paths"
    for path in statement.cited_metric_paths:
        value = resolve_path(snapshot, path)
        if value is None:
            return False, f"cited path does not resolve in snapshot: {path!r}"
        if not _value_matches_text(value, statement.text):
            return False, f"value for {path!r} ({value!r}) not found in statement text"
    return True, None


def _all_snapshot_numbers(snapshot: PortfolioMetricsSnapshot) -> list[float]:
    numbers: list[float] = []

    def walk(node: object) -> None:
        if isinstance(node, bool):
            return
        if isinstance(node, int | float):
            numbers.append(float(node))
        elif isinstance(node, dict):
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(snapshot.model_dump(mode="json"))
    return numbers


def scan_for_foreign_numbers(
    text: str, snapshot: PortfolioMetricsSnapshot, tolerance: float = _MATCH_TOLERANCE
) -> list[str]:
    """The doc's explicit second, blunter safety net: every number-shaped
    token in the *entire* statement text must match something that actually
    appears somewhere in the snapshot. Catches a number introduced without
    any citation at all. Returns the offending tokens, or [] if clean.

    Deliberately blunt: an incidental number in prose (a year, a rank) that
    happens not to match anything in the snapshot is a false positive here
    -- over-rejection is the safe failure mode (the statement just doesn't
    render), matching the doc's own "blunter safety net" framing.
    """
    snapshot_numbers = _all_snapshot_numbers(snapshot)
    foreign: list[str] = []
    for token in _NUMBER_RE.findall(text):
        try:
            candidate = float(token)
        except ValueError:
            continue
        if not any(abs(candidate - n) <= tolerance for n in snapshot_numbers):
            foreign.append(token)
    return foreign


def validate_narrative(
    response: NarrativeResponse, snapshot: PortfolioMetricsSnapshot
) -> ValidatedNarrative:
    accepted: list[GroundedStatement] = []
    rejected: list[RejectedStatement] = []

    for statement in response.statements:
        ok, reason = validate_statement(statement, snapshot)
        if not ok:
            rejected.append(RejectedStatement(statement=statement, reason=reason or "unknown"))
            continue

        foreign = scan_for_foreign_numbers(statement.text, snapshot)
        if foreign:
            rejected.append(
                RejectedStatement(
                    statement=statement,
                    reason=f"text contains number(s) absent from the snapshot: {foreign}",
                )
            )
            continue

        accepted.append(statement)

    return ValidatedNarrative(accepted=accepted, rejected=rejected)
