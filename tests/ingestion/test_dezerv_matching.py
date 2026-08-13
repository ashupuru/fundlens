from fundlens.ingestion.dezerv_matching import resolve_security
from fundlens.market_data.records import SchemeMasterRow


def _scheme(amfi_code: str, name: str, isin: str | None = "ISIN000") -> SchemeMasterRow:
    return SchemeMasterRow(
        amfi_code=amfi_code, scheme_name=name, isin_growth=isin, isin_div_reinvestment=None
    )


def test_exact_growth_match_above_floor() -> None:
    schemes = [
        _scheme("101", "Acme Focused Fund-Direct Plan-Growth"),
        _scheme("102", "Acme Focused Fund-Direct Plan-Dividend"),
    ]
    result = resolve_security("Acme Focused Direct-G", schemes)
    assert result.matched is not None
    assert result.matched.amfi_code == "101"


def test_bare_dividend_excluded_but_dividend_yield_category_kept() -> None:
    schemes = [
        _scheme("201", "Acme Dividend Yield Equity Fund Direct Plan Growth Option"),
        _scheme("202", "Acme Dividend Yield Equity Fund Direct Plan Dividend Option"),
    ]
    result = resolve_security("Acme Dividend Yield Equity Direct-G", schemes)
    assert result.matched is not None
    assert result.matched.amfi_code == "201"


def test_growth_by_omission_scheme_still_matches() -> None:
    schemes = [_scheme("301", "Acme Nifty 50 Index Fund - Direct Plan")]
    result = resolve_security("Acme Nifty 50 Index Direct-G", schemes)
    assert result.matched is not None
    assert result.matched.amfi_code == "301"


def test_near_duplicate_fund_family_disambiguated_by_extra_tokens() -> None:
    schemes = [
        _scheme("401", "Acme Midcap Fund- Direct Growth Option"),
        _scheme("402", "Acme Large & Midcap Fund - Direct Plan - Growth"),
    ]
    result = resolve_security("Acme Midcap Direct-G", schemes)
    assert result.matched is not None
    assert result.matched.amfi_code == "401"


def test_no_candidates_returns_no_candidates_reason() -> None:
    schemes = [_scheme("501", "Unrelated Overnight Fund - Direct Plan - Growth")]
    result = resolve_security("Totally Different Scheme Direct-G", schemes)
    assert result.matched is None
    assert result.reason == "no_candidates"


def test_ambiguous_tie_is_not_silently_guessed() -> None:
    schemes = [
        _scheme("601", "Acme Balanced Advantage Fund - Direct Plan - Growth"),
        _scheme("602", "Acme Balanced Advantage Fund Direct Growth"),
    ]
    result = resolve_security("Acme Balanced Advantage Direct-G", schemes)
    assert result.matched is None
    assert result.reason == "ambiguous"
    assert len(result.suggestions) == 2


def test_unrecognized_plan_suffix_not_guessed() -> None:
    schemes = [_scheme("701", "Acme Liquid Fund - Direct Plan - Growth")]
    result = resolve_security("Acme Liquid Direct-Q", schemes)
    assert result.matched is None
    assert result.reason == "unrecognized_plan_suffix"


def test_low_confidence_below_floor_reported_not_guessed() -> None:
    schemes = [_scheme("801", "Zenith Multi Asset Allocation Fund - Direct Plan - Growth")]
    result = resolve_security("Acme Midcap Direct-G", schemes)
    assert result.matched is None
    assert result.reason in {"low_confidence", "no_candidates"}
