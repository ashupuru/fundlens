"""Invariants of the tool registry.

The read-only test is the important one. An LLM that can call a mutating
function is an LLM that can silently corrupt real financial history while
sounding confident about it, so the guarantee is asserted rather than left
to reviewer memory.
"""

import inspect

import pytest
from sqlalchemy.orm import Session

from fundlens.ai.tools import TOOLS, ToolError, call_tool
from fundlens.ai.tools.serialize import compact, money, number

# Every function that writes to the database. If one of these ever appears
# in the registry, the read-only guarantee is gone.
_MUTATING_NAMES = {
    "rebuild_lots_for_folio",
    "rebuild_all_lots",
    "set_risk_profile",
    "set_retirement_profile",
    "create_goal",
    "update_goal",
    "delete_goal",
    "set_goal_holdings",
    "create_version",
    "merge_transactions",
}


def test_registry_contains_no_mutating_function() -> None:
    for name, tool in TOOLS.items():
        source = inspect.getsource(tool.fn)
        offending = sorted(n for n in _MUTATING_NAMES if n in source)
        assert not offending, f"tool {name!r} reaches mutating function(s) {offending}"


def test_no_tool_commits_or_flushes() -> None:
    for name, tool in TOOLS.items():
        source = inspect.getsource(tool.fn)
        for forbidden in ("session.commit", "session.add", "session.delete", "session.flush"):
            assert forbidden not in source, f"tool {name!r} calls {forbidden}"


def test_every_tool_schema_matches_its_callable_signature() -> None:
    """A schema advertising an argument the function can't accept turns a
    reasonable model choice into a TypeError."""
    for name, tool in TOOLS.items():
        parameters = set(inspect.signature(tool.fn).parameters)
        parameters.discard("session")
        advertised = set(tool.input_schema.get("properties", {}))
        unknown = advertised - parameters
        assert not unknown, f"{name}: schema advertises unknown argument(s) {sorted(unknown)}"

        required = set(tool.input_schema.get("required", []))
        assert required <= advertised, f"{name}: requires an argument it doesn't advertise"


def test_every_tool_has_a_description_and_object_schema() -> None:
    for name, tool in TOOLS.items():
        assert tool.description.strip(), f"{name} has no description"
        assert tool.input_schema["type"] == "object"
        assert tool.input_schema["additionalProperties"] is False


def test_api_tool_shape_is_what_the_messages_api_expects() -> None:
    tool = TOOLS["list_holdings"].to_api_tool()
    assert set(tool) == {"name", "description", "input_schema"}


def test_unknown_tool_is_rejected(session: Session) -> None:
    with pytest.raises(ToolError, match="Unknown tool"):
        call_tool(TOOLS, session, "compute_everything", {})


def test_unexpected_argument_is_rejected_before_dispatch(session: Session) -> None:
    """Caught here rather than surfacing as a TypeError from deep inside a
    compute function."""
    with pytest.raises(ToolError, match="Unexpected argument"):
        call_tool(TOOLS, session, "list_holdings", {"amfi_code": "120700"})


def test_missing_required_argument_is_rejected(session: Session) -> None:
    with pytest.raises(ToolError, match="Missing required argument"):
        call_tool(TOOLS, session, "compute_fund_metrics", {})


def test_tool_failure_is_a_tool_error_not_a_crash(session: Session) -> None:
    with pytest.raises(ToolError):
        call_tool(TOOLS, session, "compute_fund_metrics", {"amfi_code": "does-not-exist"})


def test_list_holdings_on_an_empty_portfolio(session: Session) -> None:
    result = call_tool(TOOLS, session, "list_holdings", {})
    assert result == {"holdings": [], "count": 0}


# --- serialization -------------------------------------------------------------


def test_money_is_emitted_in_all_three_indian_units() -> None:
    """So a sentence written as "₹2.42 crore" can cite a path whose value is
    2.42 -- the validator matches the number as written, and nobody writes
    corpora in paise."""
    assert money(24_225_772_700) == {"rupees": 242257727.0, "lakh": 2422.58, "crore": 24.226}


def test_money_passes_none_through() -> None:
    assert money(None) is None


def test_compact_drops_nulls() -> None:
    """A path resolving to null is indistinguishable from one that doesn't
    resolve, so null keys are removed rather than offered as citable."""
    assert compact({"a": 1, "b": None}) == {"a": 1}


def test_number_rounds_and_passes_none_through() -> None:
    assert number(3.14159) == 3.14
    assert number(3.14159, 4) == 3.1416
    assert number(None) is None
