"""Orchestration for POST /ai/ask, mirroring `ai/service.py`'s contract:
never raises to the caller. A missing API key, an Anthropic outage or a
malformed response all become a persisted row with status UNAVAILABLE and
a reason, because a portfolio tool that 500s is worse than one that says
what it couldn't do.
"""

import json
from dataclasses import dataclass
from datetime import datetime

import anthropic
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from fundlens.ai.claude_client import MODEL, GroundedStatement
from fundlens.ai.snapshot import build_portfolio_snapshot
from fundlens.ai.tool_loop import ToolCallRecord, run_ask_loop
from fundlens.ai.validator import (
    RejectedStatement,
    build_evidence,
    scan_for_foreign_numbers,
    validate_narrative,
)
from fundlens.db.models import AiAnalysisStatus, AiQuestion
from fundlens.settings import get_settings

# json.JSONDecodeError subclasses ValueError, so a malformed structured
# response is caught here too -- same set as ai/service.py.
_CLAUDE_ERRORS = (anthropic.APIError, anthropic.APIConnectionError, ValueError, ValidationError)

_NO_KEY = (
    "FUNDLENS_ANTHROPIC_API_KEY is not configured — set it in .env to enable AI answers"
)


@dataclass(frozen=True)
class AskResult:
    id: str
    created_at: datetime
    question: str
    answer_summary: str
    accepted: list[GroundedStatement]
    rejected: list[RejectedStatement]
    tool_calls: list[dict]
    status: AiAnalysisStatus
    error_message: str | None
    # Figures in `answer_summary` that don't trace to the evidence. The
    # summary carries no citations by construction, so this is the only
    # check available to it -- and the blunt scan over-rejects (a benchmark
    # name like "Nifty 50" reads as an uncited 50). Withholding the text
    # outright on that basis would delete the most useful part of the answer
    # over a false positive, so it is surfaced instead: the summary renders
    # visually distinct with these figures named, the same treatment
    # `news_context` already gets for unvalidated content (ai-layer.md §3).
    # The validated statements remain the authoritative part.
    summary_unverified_figures: list[str]


def _persist(
    session: Session,
    *,
    question: str,
    answer_summary: str,
    evidence: object,
    tool_calls: list[dict],
    accepted: list[GroundedStatement],
    rejected: list[RejectedStatement],
    status: AiAnalysisStatus,
    error_message: str | None,
    summary_unverified_figures: list[str] | None = None,
) -> AskResult:
    row = AiQuestion(
        question=question,
        answer_summary=answer_summary,
        evidence_json=json.dumps(evidence, default=str),
        tool_calls_json=json.dumps(tool_calls, default=str),
        accepted_json=json.dumps([s.model_dump() for s in accepted]),
        rejected_json=json.dumps(
            [{"statement": r.statement.model_dump(), "reason": r.reason} for r in rejected]
        ),
        model=MODEL,
        status=status,
        error_message=error_message,
    )
    session.add(row)
    session.commit()
    return AskResult(
        id=row.id,
        created_at=row.created_at,
        question=question,
        answer_summary=answer_summary,
        accepted=accepted,
        rejected=rejected,
        tool_calls=tool_calls,
        status=status,
        error_message=error_message,
        summary_unverified_figures=summary_unverified_figures or [],
    )


def _ledger(records: list[ToolCallRecord]) -> list[dict]:
    return [r.as_evidence() for r in records]


def ask_question(session: Session, question: str) -> AskResult:
    snapshot = build_portfolio_snapshot(session)
    api_key = get_settings().anthropic_api_key
    if not api_key:
        return _persist(
            session,
            question=question,
            answer_summary="",
            evidence={},
            tool_calls=[],
            accepted=[],
            rejected=[],
            status=AiAnalysisStatus.UNAVAILABLE,
            error_message=_NO_KEY,
        )

    try:
        outcome = run_ask_loop(session, api_key, question, snapshot)
    except _CLAUDE_ERRORS as exc:
        return _persist(
            session,
            question=question,
            answer_summary="",
            evidence={},
            tool_calls=[],
            accepted=[],
            rejected=[],
            status=AiAnalysisStatus.UNAVAILABLE,
            error_message=str(exc),
        )

    ledger = _ledger(outcome.tool_calls)
    evidence = build_evidence(snapshot, ledger)
    # The loop returns the same statement shape the narrative validator
    # already takes, so the determinism gate is literally the same code path
    # -- only the evidence it resolves against is wider.
    validated = validate_narrative(outcome.response, evidence)

    # Leaving the summary unchecked would put the least-verified prose in
    # the most-read place, so it gets the blunt scan -- reported, not
    # suppressed. See `AskResult.summary_unverified_figures`.
    unverified = scan_for_foreign_numbers(outcome.response.answer_summary, evidence)

    if outcome.hit_iteration_cap:
        status = AiAnalysisStatus.UNAVAILABLE
        error: str | None = "Reached the tool-call limit without producing an answer"
    elif outcome.response.statements and not validated.accepted:
        status = AiAnalysisStatus.ALL_REJECTED
        error = None
    else:
        status = AiAnalysisStatus.OK
        error = None

    return _persist(
        session,
        question=question,
        answer_summary=outcome.response.answer_summary,
        evidence=dict(evidence),
        tool_calls=ledger,
        accepted=validated.accepted,
        rejected=validated.rejected,
        status=status,
        error_message=error,
        summary_unverified_figures=unverified,
    )


def _row_to_result(row: AiQuestion) -> AskResult:
    return AskResult(
        id=row.id,
        created_at=row.created_at,
        question=row.question,
        answer_summary=row.answer_summary,
        accepted=[GroundedStatement.model_validate(s) for s in json.loads(row.accepted_json)],
        rejected=[
            RejectedStatement(
                statement=GroundedStatement.model_validate(r["statement"]), reason=r["reason"]
            )
            for r in json.loads(row.rejected_json)
        ],
        tool_calls=json.loads(row.tool_calls_json),
        status=row.status,
        error_message=row.error_message,
        # Recomputed rather than stored: the evidence is persisted verbatim,
        # so the scan is reproducible and can't drift from the text it
        # describes.
        summary_unverified_figures=scan_for_foreign_numbers(
            row.answer_summary, json.loads(row.evidence_json)
        ),
    )


def list_questions(session: Session, limit: int = 20) -> list[AskResult]:
    rows = session.scalars(
        select(AiQuestion).order_by(AiQuestion.created_at.desc()).limit(limit)
    ).all()
    return [_row_to_result(r) for r in rows]
