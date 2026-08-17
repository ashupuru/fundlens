from datetime import date

import pytest
from sqlalchemy.orm import Session

from fundlens.ai import service
from fundlens.ai.claude_client import GroundedStatement, NarrativeResponse, NewsContextItem
from fundlens.ai.snapshot import ConcentrationStats, PortfolioMetricsSnapshot
from fundlens.db.models import AiAnalysisStatus
from fundlens.settings import Settings


def _fake_snapshot(session: Session) -> PortfolioMetricsSnapshot:
    return PortfolioMetricsSnapshot(
        as_of_date=date(2024, 1, 1),
        portfolio_xirr=0.12,
        portfolio_xirr_reason=None,
        benchmark_xirr=0.10,
        benchmark_name="NIFTY50_TRI",
        alpha_pct=2.0,
        allocation=None,
        funds=[],
        overlap_pairs=[],
        fee_drag_annual_rupees=None,
        fee_drag_funds_excluded=0,
        concentration=ConcentrationStats(top_1_pct=0.0, top_3_pct=0.0, top_5_pct=0.0, hhi=0.0),
        style_drift_flags=[],
    )


@pytest.fixture(autouse=True)
def _stub_snapshot(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(service, "build_portfolio_snapshot", _fake_snapshot)


def _with_key(monkeypatch: pytest.MonkeyPatch, key: str | None = "fake-key") -> None:
    monkeypatch.setattr(service, "get_settings", lambda: Settings(anthropic_api_key=key))


def test_run_portfolio_analysis_no_api_key_configured(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _with_key(monkeypatch, None)

    result = service.run_portfolio_analysis(session)

    assert result.status == AiAnalysisStatus.UNAVAILABLE
    assert result.error_message == "ANTHROPIC_API_KEY is not configured"
    assert result.accepted == []


def test_run_portfolio_analysis_claude_call_raises(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _with_key(monkeypatch)

    def _raise(*args: object, **kwargs: object) -> NarrativeResponse:
        raise ValueError("boom")

    monkeypatch.setattr(service, "call_claude", _raise)

    result = service.run_portfolio_analysis(session)

    assert result.status == AiAnalysisStatus.UNAVAILABLE
    assert result.error_message == "boom"


def test_run_portfolio_analysis_all_accepted(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _with_key(monkeypatch)
    response = NarrativeResponse(
        statements=[
            GroundedStatement(
                topic="concentration",
                text="Your portfolio XIRR is 0.12.",
                cited_metric_paths=["portfolio_xirr"],
            )
        ],
        news_context=[
            NewsContextItem(fund_name="Some Fund", text="Manager change.", source_url="https://x")
        ],
    )
    monkeypatch.setattr(service, "call_claude", lambda *a, **kw: response)

    result = service.run_portfolio_analysis(session)

    assert result.status == AiAnalysisStatus.OK
    assert len(result.accepted) == 1
    assert result.rejected == []
    assert len(result.news_context) == 1


def test_run_portfolio_analysis_all_rejected(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _with_key(monkeypatch)
    response = NarrativeResponse(
        statements=[
            GroundedStatement(
                topic="concentration", text="Nonsense claim.", cited_metric_paths=[]
            )
        ],
        news_context=[],
    )
    monkeypatch.setattr(service, "call_claude", lambda *a, **kw: response)

    result = service.run_portfolio_analysis(session)

    assert result.status == AiAnalysisStatus.ALL_REJECTED
    assert result.accepted == []
    assert len(result.rejected) == 1


def test_run_portfolio_analysis_no_statements_is_ok_not_all_rejected(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _with_key(monkeypatch)
    response = NarrativeResponse(statements=[], news_context=[])
    monkeypatch.setattr(service, "call_claude", lambda *a, **kw: response)

    result = service.run_portfolio_analysis(session)

    assert result.status == AiAnalysisStatus.OK


def test_run_portfolio_analysis_passes_prior_summary_to_claude(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _with_key(monkeypatch)
    first_response = NarrativeResponse(
        statements=[
            GroundedStatement(
                topic="concentration",
                text="Your portfolio XIRR is 0.12.",
                cited_metric_paths=["portfolio_xirr"],
            )
        ],
        news_context=[],
    )
    monkeypatch.setattr(service, "call_claude", lambda *a, **kw: first_response)
    service.run_portfolio_analysis(session)

    captured: dict[str, object] = {}

    def _capture(
        api_key: str, snapshot: object, prior_summary: str | None = None
    ) -> NarrativeResponse:
        captured["prior_summary"] = prior_summary
        return NarrativeResponse(statements=[], news_context=[])

    monkeypatch.setattr(service, "call_claude", _capture)
    service.run_portfolio_analysis(session)

    assert captured["prior_summary"] is not None
    assert "Your portfolio XIRR is 0.12." in captured["prior_summary"]  # type: ignore[operator]


def test_get_latest_analysis_none_when_never_run(session: Session) -> None:
    assert service.get_latest_analysis(session) is None


def test_get_latest_analysis_returns_most_recent(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _with_key(monkeypatch)
    response = NarrativeResponse(
        statements=[
            GroundedStatement(
                topic="fee_drag", text="No fee drag data.", cited_metric_paths=[]
            )
        ],
        news_context=[],
    )
    monkeypatch.setattr(service, "call_claude", lambda *a, **kw: response)
    run_result = service.run_portfolio_analysis(session)

    latest = service.get_latest_analysis(session)

    assert latest is not None
    assert latest.id == run_result.id
    assert latest.status == run_result.status
