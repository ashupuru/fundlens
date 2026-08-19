from datetime import date

from fundlens.ai.claude_client import GroundedStatement, NarrativeResponse, NewsContextItem
from fundlens.ai.snapshot import ConcentrationStats, FundMetrics, PortfolioMetricsSnapshot
from fundlens.ai.validator import (
    build_evidence,
    resolve_path,
    scan_for_foreign_numbers,
    validate_narrative,
    validate_statement,
)


def _snapshot() -> PortfolioMetricsSnapshot:
    return PortfolioMetricsSnapshot(
        as_of_date=date(2024, 1, 1),
        portfolio_xirr=0.145,
        portfolio_xirr_reason=None,
        benchmark_xirr=0.10,
        benchmark_name="NIFTY50_TRI",
        alpha_pct=4.5,
        allocation=None,
        funds=[
            FundMetrics(
                scheme_name="Large Cap Fund", category="Equity", xirr=0.12, alpha_pct=1.0,
                expense_ratio=0.5, plan_type="Direct", verdict="hold", verdict_score=70.0,
                aum_crores=5000.0, groww_rating=4,
            )
        ],
        overlap_pairs=[],
        fee_drag_annual_rupees=126.0,
        fee_drag_funds_excluded=0,
        concentration=ConcentrationStats(
            top_1_pct=68.57, top_3_pct=100.0, top_5_pct=100.0, hhi=5800.0
        ),
        style_drift_flags=[],
    )


def _evidence(tool_calls: list[dict] | None = None) -> dict:
    return dict(build_evidence(_snapshot(), tool_calls or []))


def test_resolve_path_dotted_attribute() -> None:
    assert resolve_path(_evidence(), "concentration.top_1_pct") == 68.57


def test_resolve_path_list_index() -> None:
    assert resolve_path(_evidence(), "funds[0].scheme_name") == "Large Cap Fund"


def test_resolve_path_unknown_field_returns_none() -> None:
    assert resolve_path(_evidence(), "concentration.made_up_field") is None


def test_resolve_path_out_of_range_index_returns_none() -> None:
    assert resolve_path(_evidence(), "funds[5].scheme_name") is None


def test_validate_statement_accepts_grounded_claim() -> None:
    statement = GroundedStatement(
        topic="concentration",
        text="Your top holding is 68.57% of the portfolio.",
        cited_metric_paths=["concentration.top_1_pct"],
    )
    ok, reason = validate_statement(statement, _evidence())
    assert ok is True
    assert reason is None


def test_validate_statement_rejects_wrong_number_for_a_real_path() -> None:
    """The core adversarial case: a real, resolvable path is cited, but the
    number actually written in the text doesn't match it -- the exact
    hallucination shape the determinism validator exists to catch."""
    statement = GroundedStatement(
        topic="concentration",
        text="Your top holding is 95% of the portfolio.",
        cited_metric_paths=["concentration.top_1_pct"],
    )
    ok, reason = validate_statement(statement, _evidence())
    assert ok is False
    assert reason is not None and "not found in statement text" in reason


def test_validate_statement_rejects_no_citations() -> None:
    statement = GroundedStatement(
        topic="fee_drag", text="Your fees are reasonable.", cited_metric_paths=[]
    )
    ok, reason = validate_statement(statement, _evidence())
    assert ok is False
    assert reason == "statement cites no metric paths"


def test_validate_statement_rejects_nonexistent_path() -> None:
    statement = GroundedStatement(
        topic="fee_drag", text="Fees cost you Rs 999 a year.",
        cited_metric_paths=["fee_drag_annual_rupees_wrong_name"],
    )
    ok, reason = validate_statement(statement, _evidence())
    assert ok is False
    assert reason is not None and "does not resolve" in reason


def test_scan_for_foreign_numbers_flags_number_absent_from_evidence() -> None:
    foreign = scan_for_foreign_numbers("This fund returned 42% last year.", _evidence())
    assert "42" in foreign


def test_scan_for_foreign_numbers_clean_text_returns_empty() -> None:
    foreign = scan_for_foreign_numbers("Your top holding is 68.57% of the portfolio.", _evidence())
    assert foreign == []


def test_validate_narrative_adversarial_response() -> None:
    """The PRD's explicit test gate: feed the validator a deliberately
    hallucinated response and confirm every bad statement is rejected and
    logged, never silently rendered -- alongside a genuinely good one that
    must survive untouched."""
    response = NarrativeResponse(
        statements=[
            GroundedStatement(
                topic="concentration",
                text="Your top holding is 68.57% of the portfolio.",
                cited_metric_paths=["concentration.top_1_pct"],
            ),
            GroundedStatement(
                topic="concentration",
                text="Your top holding is 95% of the portfolio.",  # wrong number, real path
                cited_metric_paths=["concentration.top_1_pct"],
            ),
            GroundedStatement(
                topic="fee_drag",
                # correctly cites 126 (the real value), but ALSO claims a
                # foreign "99" nothing in the snapshot supports -- this is
                # the second, blunter safety net's job to catch, distinct
                # from the first (per-citation) check.
                text="Your fee drag is Rs 126 a year, worse than 99% of similar investors.",
                cited_metric_paths=["fee_drag_annual_rupees"],
            ),
        ],
        news_context=[
            NewsContextItem(
                fund_name="Large Cap Fund", text="Manager change announced.",
                source_url="https://example.com/news",
            )
        ],
    )

    result = validate_narrative(response, _evidence())

    assert len(result.accepted) == 1
    assert result.accepted[0].text == "Your top holding is 68.57% of the portfolio."
    assert len(result.rejected) == 2
    reasons = [r.reason for r in result.rejected]
    assert any("not found in statement text" in r for r in reasons)
    assert any("absent from the evidence" in r for r in reasons)
    # news_context is untouched -- validate_narrative never inspects it, since
    # it's explicitly allowed to reference the outside world.
    assert response.news_context[0].source_url == "https://example.com/news"


# --- tool-derived evidence -----------------------------------------------------
#
# The tool-calling layer widened what a statement may cite from "the
# snapshot" to "the snapshot plus every deterministic tool result computed
# while answering". The citation check must extend to those numbers without
# loosening -- a fabricated figure on a real tool path is exactly the
# failure this whole layer exists to prevent.

_RETIREMENT_CALL = {
    "tool": "compute_retirement_projection",
    "arguments": {"retirement_age": 52},
    "result": {"readiness_pct": 41.3, "corpus_at_retirement": {"crore": 2.42}},
}


def test_tool_result_path_resolves() -> None:
    evidence = _evidence([_RETIREMENT_CALL])

    assert resolve_path(evidence, "tool_calls[0].result.readiness_pct") == 41.3
    assert resolve_path(evidence, "tool_calls[0].result.corpus_at_retirement.crore") == 2.42


def test_tool_arguments_are_citable() -> None:
    """So "retiring at 52 leaves you short" can cite the 52 it was actually
    computed with, rather than being rejected for naming it."""
    evidence = _evidence([_RETIREMENT_CALL])

    assert resolve_path(evidence, "tool_calls[0].arguments.retirement_age") == 52


def test_statement_grounded_in_a_tool_result_is_accepted() -> None:
    statement = GroundedStatement(
        topic="concentration",
        text="Retiring at 52 leaves you 41.3% of the way to the corpus you need.",
        cited_metric_paths=[
            "tool_calls[0].arguments.retirement_age",
            "tool_calls[0].result.readiness_pct",
        ],
    )

    ok, reason = validate_statement(statement, _evidence([_RETIREMENT_CALL]))

    assert ok is True
    assert reason is None


def test_fabricated_number_on_a_real_tool_path_is_rejected() -> None:
    """The adversarial gate. The path exists and the tool really ran; the
    number in the prose is invented. Nothing about a wider evidence set may
    make this pass."""
    statement = GroundedStatement(
        topic="concentration",
        text="Retiring at 52 leaves you 88.0% of the way there.",
        cited_metric_paths=["tool_calls[0].result.readiness_pct"],
    )

    ok, reason = validate_statement(statement, _evidence([_RETIREMENT_CALL]))

    assert ok is False
    assert reason is not None
    assert "not found in statement text" in reason


def test_citing_a_tool_that_never_ran_is_rejected() -> None:
    statement = GroundedStatement(
        topic="concentration",
        text="Your readiness is 41.3%.",
        cited_metric_paths=["tool_calls[3].result.readiness_pct"],
    )

    ok, reason = validate_statement(statement, _evidence([_RETIREMENT_CALL]))

    assert ok is False
    assert reason is not None
    assert "does not resolve in evidence" in reason
