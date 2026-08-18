import pytest

from fundlens.tax.asset_class import parse_category_raw
from tests.tax.test_asset_class import DEBT_CATEGORIES, EQUITY_CATEGORIES, NEEDS_REVIEW_CATEGORIES

_CASES: list[tuple[str, tuple[str, str]]] = [
    ("Open Ended Schemes(Debt Scheme - Liquid Fund)", ("Debt", "Liquid Fund")),
    ("Open Ended Schemes(Equity Scheme - Small Cap Fund)", ("Equity", "Small Cap Fund")),
    (
        "Open Ended Schemes(Equity Schemes - Large & Mid Cap Fund)",
        ("Equity", "Large & Mid Cap Fund"),
    ),
    (
        "Open Ended Schemes(Hybrid Scheme - Aggressive Hybrid Fund)",
        ("Hybrid", "Aggressive Hybrid Fund"),
    ),
    ("Open Ended Schemes(Other Scheme - FoF Domestic)", ("Other", "FoF Domestic")),
    ("Close Ended Schemes(ELSS)", ("ELSS", "ELSS")),
]


@pytest.mark.parametrize("category_raw,expected", _CASES)
def test_parse_category_raw_common_pattern(
    category_raw: str, expected: tuple[str, str]
) -> None:
    assert parse_category_raw(category_raw) == expected


def test_parse_category_raw_no_separator_falls_back_to_whole_string() -> None:
    # Pre-2018 bare category names with no " - " split at all.
    assert parse_category_raw("Open Ended Schemes(Growth)") == ("Growth", "Growth")
    assert parse_category_raw("Open Ended Schemes(Income)") == ("Income", "Income")
    assert parse_category_raw("Open Ended Schemes(Gilt)") == ("Gilt", "Gilt")


def test_parse_category_raw_malformed_missing_outer_paren_does_not_raise() -> None:
    # AMFI's own raw feed genuinely publishes these with a missing closing
    # paren for the outer "Schemes(...)" wrapper -- must degrade gracefully,
    # never crash the category chart over a feed quirk.
    broad, specific = parse_category_raw("Open Ended Schemes(Exchange Traded Funds (ETFs)")
    assert broad and specific  # non-empty, readable -- exact text isn't load-bearing

    broad, specific = parse_category_raw(
        "Open Ended Schemes(Fund of Funds Scheme (Domestic)"
    )
    assert broad and specific


def test_parse_category_raw_no_parens_at_all_does_not_raise() -> None:
    assert parse_category_raw("Completely unstructured string") == (
        "Completely unstructured string",
        "Completely unstructured string",
    )


@pytest.mark.parametrize(
    "category_raw", EQUITY_CATEGORIES + DEBT_CATEGORIES + NEEDS_REVIEW_CATEGORIES
)
def test_parse_category_raw_never_raises_on_every_known_real_category(category_raw: str) -> None:
    """Exhaustive smoke test against the complete, real set of AMFI NAVAll
    category strings this app has ever observed (tests/tax/test_asset_class.py) --
    every one must parse to two non-empty labels, never raise."""
    broad, specific = parse_category_raw(category_raw)
    assert broad
    assert specific
