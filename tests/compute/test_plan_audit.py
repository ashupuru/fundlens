import pytest

from fundlens.compute.plan_audit import PlanType, detect_plan_type


@pytest.mark.parametrize(
    "scheme_name",
    [
        "UTI Nifty 50 Index Fund - Growth Option- Direct",
        "Motilal Oswal Nifty Midcap 150 Index Fund - Direct Plan",
        "HDFC Flexi Cap Fund - Direct Plan - Growth",
    ],
)
def test_detects_direct_plan(scheme_name: str) -> None:
    assert detect_plan_type(scheme_name) == PlanType.DIRECT


@pytest.mark.parametrize(
    "scheme_name",
    [
        "UTI Nifty 50 Index Fund - Regular Plan - Growth Option",
        "HDFC Flexi Cap Fund - Regular Plan - Growth",
    ],
)
def test_detects_regular_plan(scheme_name: str) -> None:
    assert detect_plan_type(scheme_name) == PlanType.REGULAR


def test_unknown_when_neither_marker_present() -> None:
    assert detect_plan_type("SBI BSE 100 ETF") == PlanType.UNKNOWN
