from fundlens.ingestion.zerodha_matching import resolve_isin
from fundlens.market_data.records import SchemeMasterRow


def _scheme(
    amfi_code: str, name: str, isin_growth: str | None, isin_div: str | None = None
) -> SchemeMasterRow:
    return SchemeMasterRow(
        amfi_code=amfi_code, scheme_name=name, isin_growth=isin_growth,
        isin_div_reinvestment=isin_div,
    )


def test_exact_isin_growth_match() -> None:
    schemes = [
        _scheme("151412", "Mirae Asset Flexi Cap Fund - Direct Plan - Growth", "INF769K01JJ2")
    ]
    result = resolve_isin("INF769K01JJ2", schemes)
    assert result.matched is not None
    assert result.matched.amfi_code == "151412"


def test_matches_via_div_reinvestment_isin_too() -> None:
    schemes = [
        _scheme(
            "999", "Some Fund - Direct Plan - IDCW", isin_growth="INF000A00001",
            isin_div="INF000A00002",
        )
    ]
    result = resolve_isin("INF000A00002", schemes)
    assert result.matched is not None
    assert result.matched.amfi_code == "999"


def test_no_match_reported_as_no_candidates() -> None:
    schemes = [_scheme("101", "Unrelated Fund - Direct Plan - Growth", "INF000A00001")]
    result = resolve_isin("INF999Z99999", schemes)
    assert result.matched is None
    assert result.reason == "no_candidates"


def test_isin_mapping_to_two_distinct_schemes_flagged_ambiguous_not_guessed() -> None:
    # Defensive case: AMFI's feed isn't a database with a uniqueness
    # constraint on ISIN -- if it ever happened, don't silently pick one.
    schemes = [
        _scheme("201", "Fund A - Direct Plan - Growth", "INF000A00001"),
        _scheme("202", "Fund B - Direct Plan - Growth", "INF000A00001"),
    ]
    result = resolve_isin("INF000A00001", schemes)
    assert result.matched is None
    assert result.reason == "ambiguous"


def test_same_scheme_appearing_twice_with_same_isin_is_not_ambiguous() -> None:
    # Two SchemeMasterRow entries for the *same* amfi_code (e.g. matched via
    # both isin_growth and isin_div_reinvestment fields pointing the same
    # way) must not be flagged as a false ambiguity.
    schemes = [
        _scheme("301", "Fund C - Direct Plan - Growth", "INF000A00003", isin_div="INF000A00003"),
    ]
    result = resolve_isin("INF000A00003", schemes)
    assert result.matched is not None
    assert result.matched.amfi_code == "301"
