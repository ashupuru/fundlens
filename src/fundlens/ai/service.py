"""Orchestrates the on-demand portfolio analysis run: build snapshot -> call
Claude -> validate -> persist a row -> return. Degrades to "unavailable"
rather than raising -- the rest of the app must never depend on this being
reachable (docs/architecture/ai-layer.md §5), matching every other
external-API integration in this codebase.

"Memory" is cheap: before calling Claude, the most recent prior AiAnalysis
row (if any, and if it succeeded) is summarized into the prompt so Claude
can note what's changed since last time -- no separate memory store, just
one extra SELECT against the same table this module already writes to.
"""

import json
from dataclasses import dataclass
from datetime import datetime

import anthropic
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from fundlens.ai.claude_client import MODEL, GroundedStatement, NewsContextItem, call_claude
from fundlens.ai.snapshot import build_portfolio_snapshot
from fundlens.ai.validator import RejectedStatement, build_evidence, validate_narrative
from fundlens.db.models import AiAnalysis, AiAnalysisStatus
from fundlens.settings import get_settings

_CLAUDE_ERRORS = (anthropic.APIError, anthropic.APIConnectionError, ValueError, ValidationError)


@dataclass(frozen=True)
class AiAnalysisResult:
    id: str
    created_at: datetime
    accepted: list[GroundedStatement]
    rejected: list[RejectedStatement]
    news_context: list[NewsContextItem]
    status: AiAnalysisStatus
    error_message: str | None


def _latest_row(session: Session) -> AiAnalysis | None:
    return session.scalars(
        select(AiAnalysis).order_by(AiAnalysis.created_at.desc()).limit(1)
    ).first()


def _summarize_prior(row: AiAnalysis | None) -> str | None:
    if row is None or row.status != AiAnalysisStatus.OK:
        return None
    accepted = json.loads(row.accepted_json)
    if not accepted:
        return None
    return "\n".join(f"- {s['text']}" for s in accepted)


def _row_to_result(row: AiAnalysis) -> AiAnalysisResult:
    accepted = [GroundedStatement.model_validate(s) for s in json.loads(row.accepted_json)]
    rejected = [
        RejectedStatement(
            statement=GroundedStatement.model_validate(r["statement"]), reason=r["reason"]
        )
        for r in json.loads(row.rejected_json)
    ]
    news_context = [NewsContextItem.model_validate(n) for n in json.loads(row.news_context_json)]
    return AiAnalysisResult(
        id=row.id,
        created_at=row.created_at,
        accepted=accepted,
        rejected=rejected,
        news_context=news_context,
        status=row.status,
        error_message=row.error_message,
    )


def _persist(
    session: Session,
    *,
    snapshot_json: str,
    accepted: list[GroundedStatement],
    rejected: list[RejectedStatement],
    news_context: list[NewsContextItem],
    status: AiAnalysisStatus,
    error_message: str | None,
) -> AiAnalysisResult:
    row = AiAnalysis(
        snapshot_json=snapshot_json,
        accepted_json=json.dumps([s.model_dump() for s in accepted]),
        rejected_json=json.dumps(
            [{"statement": r.statement.model_dump(), "reason": r.reason} for r in rejected]
        ),
        news_context_json=json.dumps([n.model_dump() for n in news_context]),
        model=MODEL,
        status=status,
        error_message=error_message,
    )
    session.add(row)
    session.commit()
    return _row_to_result(row)


def run_portfolio_analysis(session: Session) -> AiAnalysisResult:
    settings = get_settings()
    snapshot = build_portfolio_snapshot(session)
    snapshot_json = snapshot.model_dump_json()

    if not settings.anthropic_api_key:
        return _persist(
            session,
            snapshot_json=snapshot_json,
            accepted=[],
            rejected=[],
            news_context=[],
            status=AiAnalysisStatus.UNAVAILABLE,
            error_message="ANTHROPIC_API_KEY is not configured",
        )

    prior_summary = _summarize_prior(_latest_row(session))

    try:
        narrative = call_claude(settings.anthropic_api_key, snapshot, prior_summary)
    except _CLAUDE_ERRORS as exc:
        return _persist(
            session,
            snapshot_json=snapshot_json,
            accepted=[],
            rejected=[],
            news_context=[],
            status=AiAnalysisStatus.UNAVAILABLE,
            error_message=str(exc),
        )

    validated = validate_narrative(narrative, build_evidence(snapshot))
    status = (
        AiAnalysisStatus.ALL_REJECTED
        if narrative.statements and not validated.accepted
        else AiAnalysisStatus.OK
    )
    return _persist(
        session,
        snapshot_json=snapshot_json,
        accepted=validated.accepted,
        rejected=validated.rejected,
        news_context=narrative.news_context,
        status=status,
        error_message=None,
    )


def get_latest_analysis(session: Session) -> AiAnalysisResult | None:
    row = _latest_row(session)
    return _row_to_result(row) if row is not None else None
