import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from fundlens.compute import service
from fundlens.compute.benchmark import (
    _real_value_source,
    _simulate_benchmark_cash_flows,
    build_blended_series,
    compute_portfolio_benchmark_comparison,
    compute_portfolio_equity_blended_benchmark_comparison,
    compute_position_benchmark_comparison,
    get_all_benchmark_comparisons,
)
from fundlens.compute.holdings import HoldingView
from fundlens.db.models import (
    BenchmarkHistory,
    BenchmarkId,
    NavHistory,
    NavSource,
    RawCASUpload,
    RawTransaction,
    SchemeMetadata,
    TransactionType,
    UploadStatus,
)
from fundlens.tax.asset_class import TaxAssetClass


def _make_upload(session: Session) -> RawCASUpload:
    upload = RawCASUpload(
        file_hash=f"hash-{uuid.uuid4().hex}", parser_version="test", status=UploadStatus.PARSED
    )
    session.add(upload)
    session.flush()
    return upload


def _add_txn(
    session: Session,
    upload_id: str,
    *,
    folio: str = "F1",
    scheme_name: str = "Fund A",
    amc: str = "Test AMC",
    amfi_code: str | None = "120716",
    isin: str | None = "ISIN111",
    txn_type: TransactionType,
    txn_date: date,
    sequence: int = 0,
    units: Decimal,
    amount_paise: int,
) -> RawTransaction:
    txn = RawTransaction(
        upload_id=upload_id,
        folio=folio,
        amc=amc,
        scheme_name=scheme_name,
        amfi_code=amfi_code,
        isin=isin,
        txn_type=txn_type,
        txn_date=txn_date,
        sequence=sequence,
        units=units,
        amount_paise=amount_paise,
        natural_key=f"key-{uuid.uuid4().hex}",
    )
    session.add(txn)
    return txn


def _add_benchmark_value(
    session: Session, benchmark_id: BenchmarkId, nav_date: date, value: str
) -> None:
    session.add(
        BenchmarkHistory(
            benchmark_id=benchmark_id,
            nav_date=nav_date,
            value=Decimal(value),
            proxy=True,
            proxy_note="test proxy",
        )
    )


def _open_holding(units_remaining: str) -> HoldingView:
    return HoldingView(
        folio="F1",
        scheme_key="ISIN111",
        scheme_name="Fund A",
        amc="Test AMC",
        pan=None,
        isin="ISIN111",
        amfi_code="120716",
        units_remaining=Decimal(units_remaining),
        cost_basis_paise=0,
        average_cost_per_unit=Decimal("0"),
    )


def test_single_purchase_still_held_matches_hand_computed_xirr(session: Session) -> None:
    # Hand calc: invest 10,000 on 2023-01-01 when benchmark=100 (100 units
    # bought). Latest benchmark value 2024-01-01 = 120 -> terminal = 12,000.
    # Exactly 365 days apart (not spanning a leap day) -> XIRR = 12000/10000
    # - 1 = 0.20 exactly.
    upload = _make_upload(session)
    txn = _add_txn(
        session, upload.id, txn_type=TransactionType.PURCHASE, txn_date=date(2023, 1, 1),
        units=Decimal("100"), amount_paise=1_000_000,
    )
    _add_benchmark_value(session, BenchmarkId.NIFTY50_TRI, date(2023, 1, 1), "100")
    _add_benchmark_value(session, BenchmarkId.NIFTY50_TRI, date(2024, 1, 1), "120")
    session.commit()

    result = compute_position_benchmark_comparison(
        session,
        folio="F1",
        key="ISIN111",
        transactions=[txn],
        open_holding=_open_holding("100"),
        fund_xirr=0.30,
        fund_xirr_reason=None,
        override_benchmark_id=BenchmarkId.NIFTY50_TRI,
    )

    assert result.benchmark_xirr is not None
    assert abs(result.benchmark_xirr - 0.20) < 1e-6
    assert result.alpha_pct is not None
    assert abs(result.alpha_pct - 10.0) < 1e-4  # (0.30 - 0.20) * 100


def test_full_exit_liquidates_proportional_benchmark_units_not_real_proceeds(
    session: Session,
) -> None:
    # Buy 50 units for 10,000 on 2023-01-01 @ benchmark=100 -> 100 benchmark
    # units. Fully exit (sell all 50 units) on 2023-07-01 @ benchmark=150 for
    # a REAL 6,000 -- but the fund's real proceeds must NOT leak onto the
    # benchmark side (that was the P1.1 real-data-caught bug: doing so makes
    # benchmark_xirr trivially equal fund_xirr for every fully-exited
    # position). The benchmark side instead liquidates 100% of its 100 units
    # (same fraction as the fund exited) @ 150 = 15,000, giving cash flows
    # [(-10000, 2023-01-01), (+15000, 2023-07-01)] -> hand/solver-verified
    # XIRR = 1.2651718598563797 (126.5%), NOT the ~-19% a naive 6,000 exit
    # would produce, and NOT equal to fund_xirr.
    upload = _make_upload(session)
    buy = _add_txn(
        session, upload.id, txn_type=TransactionType.PURCHASE, txn_date=date(2023, 1, 1),
        units=Decimal("50"), amount_paise=1_000_000, sequence=0,
    )
    sell = _add_txn(
        session, upload.id, txn_type=TransactionType.REDEMPTION, txn_date=date(2023, 7, 1),
        units=Decimal("-50"), amount_paise=-600_000, sequence=1,
    )
    _add_benchmark_value(session, BenchmarkId.NIFTY50_TRI, date(2023, 1, 1), "100")
    _add_benchmark_value(session, BenchmarkId.NIFTY50_TRI, date(2023, 7, 1), "150")
    session.commit()

    result = compute_position_benchmark_comparison(
        session,
        folio="F1",
        key="ISIN111",
        transactions=[buy, sell],
        open_holding=_open_holding("0"),
        fund_xirr=-0.19,  # what the fund's real (6,000 in, 10,000 out) loss actually is
        fund_xirr_reason=None,
        override_benchmark_id=BenchmarkId.NIFTY50_TRI,
    )

    assert result.benchmark_xirr is not None
    assert result.benchmark_xirr_reason is None
    assert abs(result.benchmark_xirr - 1.2651718598563797) < 1e-6
    assert result.fund_xirr is not None
    assert abs(result.benchmark_xirr - result.fund_xirr) > 0.5  # not accidentally equal


def test_partial_redemption_liquidates_only_the_sold_fraction(session: Session) -> None:
    # Buy 100 units for 10,000 @ benchmark=100 -> 100 benchmark units. Sell
    # HALF the fund units (50 of 100) later @ benchmark=120 -> liquidate
    # exactly half the benchmark units (50) @ 120 = 6,000, leaving 50
    # benchmark units for the terminal flow.
    upload = _make_upload(session)
    buy = _add_txn(
        session, upload.id, txn_type=TransactionType.PURCHASE, txn_date=date(2023, 1, 1),
        units=Decimal("100"), amount_paise=1_000_000, sequence=0,
    )
    sell = _add_txn(
        session, upload.id, txn_type=TransactionType.REDEMPTION, txn_date=date(2023, 4, 1),
        units=Decimal("-50"), amount_paise=-550_000, sequence=1,
    )
    _add_benchmark_value(session, BenchmarkId.NIFTY50_TRI, date(2023, 1, 1), "100")
    _add_benchmark_value(session, BenchmarkId.NIFTY50_TRI, date(2023, 4, 1), "120")
    session.commit()

    flows, reason = _simulate_benchmark_cash_flows(
        [buy, sell], _open_holding("50"), _real_value_source(session, BenchmarkId.NIFTY50_TRI)
    )

    assert reason is None
    redemption_flow = next(f for f in flows if f.date == date(2023, 4, 1))
    assert redemption_flow.amount == Decimal("6000")  # 50 units * 120, not the real 5,500


def test_dividend_payout_excluded_from_benchmark_timeline(session: Session) -> None:
    upload = _make_upload(session)
    buy = _add_txn(
        session, upload.id, txn_type=TransactionType.PURCHASE, txn_date=date(2023, 1, 1),
        units=Decimal("100"), amount_paise=1_000_000, sequence=0,
    )
    dividend = _add_txn(
        session, upload.id, txn_type=TransactionType.DIVIDEND_PAYOUT, txn_date=date(2023, 3, 1),
        units=None, amount_paise=50_000, sequence=1,
    )
    _add_benchmark_value(session, BenchmarkId.NIFTY50_TRI, date(2023, 1, 1), "100")
    _add_benchmark_value(session, BenchmarkId.NIFTY50_TRI, date(2023, 3, 1), "110")
    _add_benchmark_value(session, BenchmarkId.NIFTY50_TRI, date(2024, 1, 1), "130")
    session.commit()

    flows, reason = _simulate_benchmark_cash_flows(
        [buy, dividend], _open_holding("100"), _real_value_source(session, BenchmarkId.NIFTY50_TRI)
    )

    assert reason is None
    # Only the purchase (+terminal, no BenchmarkHistory row at "today" here
    # since _latest_benchmark_value just picks the latest inserted row) shows
    # up -- the dividend payout produces no benchmark-side flow at all.
    assert all(f.date != date(2023, 3, 1) for f in flows)


def test_no_category_benchmark_when_scheme_metadata_missing(session: Session) -> None:
    upload = _make_upload(session)
    txn = _add_txn(
        session, upload.id, amfi_code="999999", txn_type=TransactionType.PURCHASE,
        txn_date=date(2023, 1, 1), units=Decimal("100"), amount_paise=1_000_000,
    )
    session.commit()

    result = compute_position_benchmark_comparison(
        session,
        folio="F1",
        key="ISIN111",
        transactions=[txn],
        open_holding=_open_holding("100"),
        fund_xirr=0.1,
        fund_xirr_reason=None,
    )

    assert result.benchmark_id is None
    assert result.benchmark_xirr_reason == "no_category_benchmark"
    assert result.alpha_pct is None


def test_debt_scheme_has_no_benchmark_comparison(session: Session) -> None:
    upload = _make_upload(session)
    session.add(
        SchemeMetadata(
            amfi_code="120716",
            category_raw="Open Ended Schemes(Debt Scheme - Liquid Fund)",
            tax_asset_class=TaxAssetClass.DEBT,
        )
    )
    txn = _add_txn(
        session, upload.id, txn_type=TransactionType.PURCHASE, txn_date=date(2023, 1, 1),
        units=Decimal("100"), amount_paise=1_000_000,
    )
    session.commit()

    result = compute_position_benchmark_comparison(
        session,
        folio="F1",
        key="ISIN111",
        transactions=[txn],
        open_holding=_open_holding("100"),
        fund_xirr=0.1,
        fund_xirr_reason=None,
    )

    assert result.benchmark_id is None
    assert result.benchmark_xirr_reason == "no_category_benchmark"


def test_benchmark_history_unavailable_when_no_value_for_flow_date(session: Session) -> None:
    upload = _make_upload(session)
    txn = _add_txn(
        session, upload.id, txn_type=TransactionType.PURCHASE, txn_date=date(2023, 1, 1),
        units=Decimal("100"), amount_paise=1_000_000,
    )
    session.commit()  # no BenchmarkHistory rows inserted at all

    result = compute_position_benchmark_comparison(
        session,
        folio="F1",
        key="ISIN111",
        transactions=[txn],
        open_holding=_open_holding("100"),
        fund_xirr=0.1,
        fund_xirr_reason=None,
        override_benchmark_id=BenchmarkId.NIFTY50_TRI,
    )

    assert result.benchmark_xirr is None
    assert result.benchmark_xirr_reason == "benchmark_history_unavailable"


def test_category_default_selects_midcap_benchmark(session: Session) -> None:
    upload = _make_upload(session)
    session.add(
        SchemeMetadata(
            amfi_code="120716",
            category_raw="Open Ended Schemes(Equity Scheme - Mid Cap Fund)",
            tax_asset_class=TaxAssetClass.EQUITY,
        )
    )
    txn = _add_txn(
        session, upload.id, txn_type=TransactionType.PURCHASE, txn_date=date(2023, 1, 1),
        units=Decimal("100"), amount_paise=1_000_000,
    )
    _add_benchmark_value(session, BenchmarkId.NIFTY_MIDCAP150_TRI, date(2023, 1, 1), "100")
    _add_benchmark_value(session, BenchmarkId.NIFTY_MIDCAP150_TRI, date(2024, 1, 1), "110")
    session.commit()

    result = compute_position_benchmark_comparison(
        session,
        folio="F1",
        key="ISIN111",
        transactions=[txn],
        open_holding=_open_holding("100"),
        fund_xirr=0.1,
        fund_xirr_reason=None,
    )

    assert result.benchmark_id == BenchmarkId.NIFTY_MIDCAP150_TRI
    assert result.proxy_note is not None


def test_get_all_benchmark_comparisons_empty_when_no_positions(session: Session) -> None:
    assert get_all_benchmark_comparisons(session) == []


def test_portfolio_comparison_empty_portfolio(session: Session) -> None:
    result = compute_portfolio_benchmark_comparison(session)
    assert result.portfolio_xirr is None
    assert result.benchmark_id == BenchmarkId.NIFTY50_TRI


# ---------- build_blended_series ----------


def test_build_blended_series_combines_weighted_daily_returns(session: Session) -> None:
    # Component A (weight 60): +10% on 2023-01-02. Component B (weight 40): -5%.
    # Hand calc: blended return = 0.6*0.10 + 0.4*(-0.05) = 0.04 -> level 1000*1.04 = 1040.
    _add_benchmark_value(session, BenchmarkId.NIFTY50_TRI, date(2023, 1, 1), "100")
    _add_benchmark_value(session, BenchmarkId.NIFTY50_TRI, date(2023, 1, 2), "110")
    _add_benchmark_value(session, BenchmarkId.NIFTY_MIDCAP150_TRI, date(2023, 1, 1), "200")
    _add_benchmark_value(session, BenchmarkId.NIFTY_MIDCAP150_TRI, date(2023, 1, 2), "190")
    session.commit()

    blended = build_blended_series(
        session,
        {BenchmarkId.NIFTY50_TRI: Decimal("60"), BenchmarkId.NIFTY_MIDCAP150_TRI: Decimal("40")},
    )

    assert blended.start_date == date(2023, 1, 2)
    assert len(blended.values) == 1
    d, value = blended.values[0]
    assert d == date(2023, 1, 2)
    assert value == Decimal("1000") * Decimal("1.04")
    assert blended.excluded_components == []


def test_build_blended_series_excludes_zero_weight_components(session: Session) -> None:
    _add_benchmark_value(session, BenchmarkId.NIFTY50_TRI, date(2023, 1, 1), "100")
    _add_benchmark_value(session, BenchmarkId.NIFTY50_TRI, date(2023, 1, 2), "110")
    session.commit()

    blended = build_blended_series(
        session,
        {BenchmarkId.NIFTY50_TRI: Decimal("100"), BenchmarkId.NIFTY_SMALLCAP250_TRI: Decimal("0")},
    )

    assert blended.excluded_components == [BenchmarkId.NIFTY_SMALLCAP250_TRI]
    assert blended.start_date == date(2023, 1, 2)


def test_build_blended_series_starts_at_latest_common_date(session: Session) -> None:
    # Nifty50-style long history vs a Smallcap-style proxy that starts later
    # (real-data-verified pattern: proxy_config.py's own documented gap).
    _add_benchmark_value(session, BenchmarkId.NIFTY50_TRI, date(2019, 1, 1), "100")
    _add_benchmark_value(session, BenchmarkId.NIFTY50_TRI, date(2023, 6, 1), "150")
    _add_benchmark_value(session, BenchmarkId.NIFTY50_TRI, date(2024, 1, 1), "160")
    _add_benchmark_value(session, BenchmarkId.NIFTY_SMALLCAP250_TRI, date(2023, 6, 1), "50")
    _add_benchmark_value(session, BenchmarkId.NIFTY_SMALLCAP250_TRI, date(2024, 1, 1), "55")
    session.commit()

    blended = build_blended_series(
        session,
        {BenchmarkId.NIFTY50_TRI: Decimal("50"), BenchmarkId.NIFTY_SMALLCAP250_TRI: Decimal("50")},
    )

    # Smallcap's first return is on 2024-01-01 (its first point, 2023-06-01,
    # has no prior value to compute a return from) -- that's the only common
    # return date, even though Nifty50 alone has a return on 2023-06-01 too.
    assert blended.start_date == date(2024, 1, 1)


def test_build_blended_series_empty_when_no_overlap(session: Session) -> None:
    _add_benchmark_value(session, BenchmarkId.NIFTY50_TRI, date(2019, 1, 1), "100")
    _add_benchmark_value(session, BenchmarkId.NIFTY50_TRI, date(2019, 1, 2), "101")
    _add_benchmark_value(session, BenchmarkId.NIFTY_SMALLCAP250_TRI, date(2023, 1, 1), "50")
    _add_benchmark_value(session, BenchmarkId.NIFTY_SMALLCAP250_TRI, date(2023, 1, 2), "51")
    session.commit()

    blended = build_blended_series(
        session,
        {BenchmarkId.NIFTY50_TRI: Decimal("50"), BenchmarkId.NIFTY_SMALLCAP250_TRI: Decimal("50")},
    )

    assert blended.values == []
    assert blended.start_date is None


def test_build_blended_series_all_zero_weight_is_empty(session: Session) -> None:
    blended = build_blended_series(session, {BenchmarkId.NIFTY50_TRI: Decimal("0")})

    assert blended.values == []
    assert blended.start_date is None
    assert blended.excluded_components == [BenchmarkId.NIFTY50_TRI]


# ---------- compute_portfolio_equity_blended_benchmark_comparison ----------


def _add_large_cap_metadata(session: Session, amfi_code: str) -> None:
    session.add(
        SchemeMetadata(
            amfi_code=amfi_code,
            category_raw="Open Ended Schemes(Equity Scheme - Large Cap Fund)",
            tax_asset_class=TaxAssetClass.EQUITY,
        )
    )


def _add_mid_cap_metadata(session: Session, amfi_code: str) -> None:
    session.add(
        SchemeMetadata(
            amfi_code=amfi_code,
            category_raw="Open Ended Schemes(Equity Scheme - Mid Cap Fund)",
            tax_asset_class=TaxAssetClass.EQUITY,
        )
    )


def test_equity_blended_comparison_no_equity_holdings(session: Session) -> None:
    result = compute_portfolio_equity_blended_benchmark_comparison(session)

    assert result.xirr_reason == "no_equity_holdings"
    assert result.benchmark_xirr_reason == "no_equity_holdings"
    assert result.large_weight_pct == 0.0
    assert result.blended_series_start_date is None


def test_equity_blended_comparison_single_component_matches_flat_benchmark(
    session: Session,
) -> None:
    # All-large-cap portfolio: the blend degenerates to Nifty50 alone (its
    # weight normalizes to 1.0), so the blended-benchmark XIRR must be
    # mathematically identical to a plain single-benchmark comparison --
    # XIRR is scale-invariant, so rebasing to 1000 vs Nifty50's own raw
    # levels changes nothing about the computed rate. Strong correctness
    # check: this isn't just "runs without crashing", it's "produces the
    # exact same number as the already-trusted single-benchmark path".
    upload = _make_upload(session)
    _add_large_cap_metadata(session, "111")
    _add_txn(
        session, upload.id, amfi_code="111", isin="ISIN111",
        txn_type=TransactionType.PURCHASE, txn_date=date(2023, 1, 1),
        units=Decimal("100"), amount_paise=1_000_000,
    )
    # An earlier point so NIFTY50_TRI already has a *return* (not just a raw
    # value) as of 2023-01-01 -- build_blended_series's series starts at the
    # first return date, one point later than the raw series' first date,
    # so the blend needs to cover the purchase date itself.
    _add_benchmark_value(session, BenchmarkId.NIFTY50_TRI, date(2022, 6, 1), "90")
    _add_benchmark_value(session, BenchmarkId.NIFTY50_TRI, date(2023, 1, 1), "100")
    _add_benchmark_value(session, BenchmarkId.NIFTY50_TRI, date(2023, 6, 1), "110")
    _add_benchmark_value(session, BenchmarkId.NIFTY50_TRI, date(2024, 1, 1), "120")
    # The scheme's own NAV (distinct from the benchmark's value above) --
    # compute_portfolio_valuation needs this to price the holding at all.
    session.add(
        NavHistory(
            amfi_code="111", nav_date=date(2024, 1, 1), nav=Decimal("105"),
            source=NavSource.MANUAL,
        )
    )
    session.commit()
    service.rebuild_all_lots(session)

    flat = compute_portfolio_benchmark_comparison(session, BenchmarkId.NIFTY50_TRI)
    blended = compute_portfolio_equity_blended_benchmark_comparison(session)

    assert blended.large_weight_pct == pytest.approx(100.0)
    assert blended.mid_weight_pct == 0.0
    assert blended.small_weight_pct == 0.0
    assert blended.benchmark_xirr is not None
    assert flat.benchmark_xirr is not None
    assert blended.benchmark_xirr == pytest.approx(flat.benchmark_xirr, rel=1e-9)
    assert blended.xirr == pytest.approx(flat.portfolio_xirr, rel=1e-9)


def test_equity_blended_comparison_weights_proportional_to_cap_value(session: Session) -> None:
    upload = _make_upload(session)
    _add_large_cap_metadata(session, "111")
    _add_mid_cap_metadata(session, "222")
    _add_txn(
        session, upload.id, folio="F1", amfi_code="111", isin="ISIN111",
        scheme_name="Large Fund", txn_type=TransactionType.PURCHASE,
        txn_date=date(2023, 1, 1), units=Decimal("100"), amount_paise=1_000_000,
    )
    _add_txn(
        session, upload.id, folio="F1", amfi_code="222", isin="ISIN222",
        scheme_name="Mid Fund", txn_type=TransactionType.PURCHASE,
        txn_date=date(2023, 1, 1), units=Decimal("50"), amount_paise=500_000,
    )
    _add_benchmark_value(session, BenchmarkId.NIFTY50_TRI, date(2023, 1, 1), "100")
    _add_benchmark_value(session, BenchmarkId.NIFTY50_TRI, date(2024, 1, 1), "120")
    _add_benchmark_value(session, BenchmarkId.NIFTY_MIDCAP150_TRI, date(2023, 1, 1), "100")
    _add_benchmark_value(session, BenchmarkId.NIFTY_MIDCAP150_TRI, date(2024, 1, 1), "130")
    # NAV so compute_portfolio_valuation can price both open holdings --
    # equal NAV=cost (100/unit) for both keeps the value split at the same
    # 2:1 ratio as the original purchase amounts (10,000 vs 5,000).
    session.add(
        NavHistory(
            amfi_code="111", nav_date=date(2024, 1, 1), nav=Decimal("100"),
            source=NavSource.MANUAL,
        )
    )
    session.add(
        NavHistory(
            amfi_code="222", nav_date=date(2024, 1, 1), nav=Decimal("100"),
            source=NavSource.MANUAL,
        )
    )
    session.commit()
    service.rebuild_all_lots(session)

    result = compute_portfolio_equity_blended_benchmark_comparison(session)

    assert result.large_weight_pct == pytest.approx(200 / 3)  # 10,000 / 15,000
    assert result.mid_weight_pct == pytest.approx(100 / 3)  # 5,000 / 15,000
    assert result.small_weight_pct == 0.0
