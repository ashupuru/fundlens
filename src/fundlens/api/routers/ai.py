"""PRD FR-5.x: on-demand portfolio health narrative (docs/architecture/ai-layer.md).
Rejected-statement detail is intentionally never exposed here -- per the doc,
a dropped statement is logged for audit, not surfaced to the user; it's only
readable via `rejected_json` directly in the DB."""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from fundlens.ai.ask_service import AskResult, ask_question, list_questions
from fundlens.ai.service import AiAnalysisResult, get_latest_analysis, run_portfolio_analysis
from fundlens.api.deps import get_session

router = APIRouter(prefix="/ai", tags=["ai"])


class GroundedStatementResponse(BaseModel):
    topic: str
    text: str
    cited_metric_paths: list[str]


class NewsContextItemResponse(BaseModel):
    fund_name: str
    text: str
    source_url: str | None


class AiAnalysisResponse(BaseModel):
    id: str
    created_at: str
    accepted: list[GroundedStatementResponse]
    news_context: list[NewsContextItemResponse]
    status: str
    error_message: str | None


def _to_response(result: AiAnalysisResult) -> AiAnalysisResponse:
    return AiAnalysisResponse(
        id=result.id,
        created_at=result.created_at.isoformat(),
        accepted=[
            GroundedStatementResponse(
                topic=s.topic, text=s.text, cited_metric_paths=s.cited_metric_paths
            )
            for s in result.accepted
        ],
        news_context=[
            NewsContextItemResponse(fund_name=n.fund_name, text=n.text, source_url=n.source_url)
            for n in result.news_context
        ],
        status=result.status.value,
        error_message=result.error_message,
    )


@router.post("/analyze", response_model=AiAnalysisResponse)
def analyze(session: Session = Depends(get_session)) -> AiAnalysisResponse:
    return _to_response(run_portfolio_analysis(session))


@router.get("/latest", response_model=AiAnalysisResponse)
def latest(session: Session = Depends(get_session)) -> AiAnalysisResponse:
    result = get_latest_analysis(session)
    if result is None:
        raise HTTPException(status_code=404, detail="No analysis has been run yet")
    return _to_response(result)


class AskRequest(BaseModel):
    question: str


class ToolCallResponse(BaseModel):
    tool: str
    arguments: dict[str, Any]
    result: Any | None = None
    error: str | None = None


class AskResponseModel(BaseModel):
    id: str
    created_at: str
    question: str
    answer_summary: str
    statements: list[GroundedStatementResponse]
    tool_calls: list[ToolCallResponse]
    status: str
    error_message: str | None
    summary_unverified_figures: list[str]


def _ask_to_response(result: AskResult) -> AskResponseModel:
    return AskResponseModel(
        id=result.id,
        created_at=result.created_at.isoformat(),
        question=result.question,
        answer_summary=result.answer_summary,
        statements=[
            GroundedStatementResponse(
                topic=s.topic, text=s.text, cited_metric_paths=s.cited_metric_paths
            )
            for s in result.accepted
        ],
        # The chain is surfaced, unlike `rejected`: which computations ran
        # with which arguments is exactly what makes a tool-derived answer
        # checkable rather than something to take on faith.
        tool_calls=[ToolCallResponse(**call) for call in result.tool_calls],
        status=result.status.value,
        error_message=result.error_message,
        summary_unverified_figures=result.summary_unverified_figures,
    )


@router.post("/ask", response_model=AskResponseModel)
def ask(request: AskRequest, session: Session = Depends(get_session)) -> AskResponseModel:
    question = request.question.strip()
    if not question:
        raise HTTPException(status_code=422, detail="Question must not be empty")
    return _ask_to_response(ask_question(session, question))


@router.get("/questions", response_model=list[AskResponseModel])
def questions(session: Session = Depends(get_session)) -> list[AskResponseModel]:
    return [_ask_to_response(r) for r in list_questions(session)]
