import json
from datetime import date

import anthropic
import pytest

from fundlens.ai.claude_client import MODEL, call_claude
from fundlens.ai.snapshot import ConcentrationStats, PortfolioMetricsSnapshot


def _empty_snapshot() -> PortfolioMetricsSnapshot:
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


class _FakeTextBlock:
    def __init__(self, text: str) -> None:
        self.type = "text"
        self.text = text


class _FakeResponse:
    def __init__(self, content: list[_FakeTextBlock]) -> None:
        self.content = content


class _FakeMessages:
    def __init__(self, response: _FakeResponse) -> None:
        self._response = response
        self.last_kwargs: dict | None = None

    def create(self, **kwargs: object) -> _FakeResponse:
        self.last_kwargs = kwargs
        return self._response


class _FakeAnthropicClient:
    def __init__(self, messages: _FakeMessages) -> None:
        self.messages = messages


def _install_fake_client(monkeypatch: pytest.MonkeyPatch, response_json: dict) -> _FakeMessages:
    fake_messages = _FakeMessages(_FakeResponse([_FakeTextBlock(json.dumps(response_json))]))
    monkeypatch.setattr(
        anthropic, "Anthropic", lambda api_key: _FakeAnthropicClient(fake_messages)
    )
    return fake_messages


def test_call_claude_parses_valid_response(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_client(
        monkeypatch,
        {
            "statements": [
                {
                    "topic": "concentration",
                    "text": "Your top holding is 68.6% of the portfolio.",
                    "cited_metric_paths": ["concentration.top_1_pct"],
                }
            ],
            "news_context": [
                {
                    "fund_name": "Large Cap Fund",
                    "text": "The fund house announced a new manager.",
                    "source_url": "https://example.com/news",
                }
            ],
        },
    )

    result = call_claude("fake-key", _empty_snapshot())

    assert len(result.statements) == 1
    assert result.statements[0].topic == "concentration"
    assert len(result.news_context) == 1
    assert result.news_context[0].source_url == "https://example.com/news"


def test_call_claude_request_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_messages = _install_fake_client(monkeypatch, {"statements": [], "news_context": []})

    call_claude("fake-key", _empty_snapshot(), prior_summary="Last time: XIRR was 10%.")

    kwargs = fake_messages.last_kwargs
    assert kwargs is not None
    assert kwargs["model"] == MODEL
    assert kwargs["thinking"] == {"type": "adaptive"}
    tools = kwargs["tools"]
    assert tools[0]["type"] == "web_search_20260209"
    assert tools[0]["max_uses"] == 3
    assert kwargs["output_config"]["format"]["type"] == "json_schema"
    user_content = kwargs["messages"][0]["content"]
    assert "Last time: XIRR was 10%." in user_content


def test_call_claude_raises_on_no_text_content(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        anthropic,
        "Anthropic",
        lambda api_key: _FakeAnthropicClient(_FakeMessages(_FakeResponse([]))),
    )

    with pytest.raises(ValueError, match="no text content"):
        call_claude("fake-key", _empty_snapshot())


def test_call_claude_propagates_malformed_json(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_messages = _FakeMessages(_FakeResponse([_FakeTextBlock("not valid json")]))
    monkeypatch.setattr(
        anthropic, "Anthropic", lambda api_key: _FakeAnthropicClient(fake_messages)
    )

    with pytest.raises(json.JSONDecodeError):
        call_claude("fake-key", _empty_snapshot())
