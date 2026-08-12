from datetime import date
from decimal import Decimal

import pytest

from fundlens.compute.lots import build_lots
from fundlens.compute.records import InsufficientLotsError, LotInputTxn


def _txn(
    id: str,
    txn_date: date,
    txn_type: str,
    units: Decimal | None,
    nav: Decimal | None = None,
    amount_paise: int | None = None,
) -> LotInputTxn:
    return LotInputTxn(
        id=id, txn_date=txn_date, txn_type=txn_type, units=units, nav=nav, amount_paise=amount_paise
    )


def test_single_purchase_opens_one_lot() -> None:
    txns = [_txn("t1", date(2021, 4, 1), "PURCHASE", Decimal("100"), nav=Decimal("50"))]

    result = build_lots(txns)

    assert len(result.lots) == 1
    lot = result.lots[0]
    assert lot.units_remaining == Decimal("100")
    assert lot.cost_per_unit == Decimal("50")
    assert lot.source_txn_id == "t1"
    assert lot.close_date is None
    assert result.realized_gains == []


def test_full_redemption_closes_the_lot_and_records_realized_gain() -> None:
    txns = [
        _txn("t1", date(2021, 4, 1), "PURCHASE", Decimal("100"), nav=Decimal("50")),
        _txn("t2", date(2021, 5, 1), "REDEMPTION", Decimal("-100"), nav=Decimal("60")),
    ]

    result = build_lots(txns)

    assert result.lots[0].units_remaining == Decimal("0")
    assert result.lots[0].close_date == date(2021, 5, 1)
    assert len(result.realized_gains) == 1
    gain = result.realized_gains[0]
    assert gain.units_consumed == Decimal("100")
    assert gain.cost_per_unit == Decimal("50")
    assert gain.sale_price_per_unit == Decimal("60")
    assert gain.realized_gain_paise == 100_000  # (60-50)*100 = 1000 rupees = 100,000 paise
    assert gain.holding_period_days == 30


def test_partial_redemption_leaves_lot_open_with_reduced_units() -> None:
    txns = [
        _txn("t1", date(2021, 4, 1), "PURCHASE", Decimal("100"), nav=Decimal("50")),
        _txn("t2", date(2021, 5, 1), "REDEMPTION", Decimal("-40"), nav=Decimal("60")),
    ]

    result = build_lots(txns)

    assert result.lots[0].units_remaining == Decimal("60")
    assert result.lots[0].close_date is None
    assert result.realized_gains[0].units_consumed == Decimal("40")


def test_redemption_spanning_multiple_lots_consumes_oldest_first() -> None:
    txns = [
        _txn("t1", date(2021, 1, 1), "PURCHASE", Decimal("50"), nav=Decimal("10")),
        _txn("t2", date(2021, 2, 1), "PURCHASE", Decimal("50"), nav=Decimal("20")),
        _txn("t3", date(2021, 3, 1), "REDEMPTION", Decimal("-70"), nav=Decimal("30")),
    ]

    result = build_lots(txns)

    # First lot (50 units @ 10) fully consumed, second lot (50 @ 20) partially
    # consumed for the remaining 20 units — classic FIFO.
    assert len(result.realized_gains) == 2
    first_slice, second_slice = result.realized_gains
    assert first_slice.source_txn_id == "t1"
    assert first_slice.units_consumed == Decimal("50")
    assert second_slice.source_txn_id == "t2"
    assert second_slice.units_consumed == Decimal("20")

    lot1, lot2 = result.lots
    assert lot1.units_remaining == Decimal("0")
    assert lot1.close_date == date(2021, 3, 1)
    assert lot2.units_remaining == Decimal("30")
    assert lot2.close_date is None


def test_sip_purchases_open_separate_lots() -> None:
    txns = [
        _txn("t1", date(2021, 1, 5), "PURCHASE_SIP", Decimal("10"), nav=Decimal("100")),
        _txn("t2", date(2021, 2, 5), "PURCHASE_SIP", Decimal("10"), nav=Decimal("110")),
        _txn("t3", date(2021, 3, 5), "PURCHASE_SIP", Decimal("10"), nav=Decimal("105")),
    ]

    result = build_lots(txns)

    assert len(result.lots) == 3
    assert [lot.cost_per_unit for lot in result.lots] == [
        Decimal("100"),
        Decimal("110"),
        Decimal("105"),
    ]


def test_dividend_reinvest_and_switch_in_open_lots_like_a_purchase() -> None:
    txns = [
        _txn("t1", date(2021, 1, 1), "DIVIDEND_REINVEST", Decimal("5"), nav=Decimal("20")),
        _txn("t2", date(2021, 2, 1), "SWITCH_IN", Decimal("15"), nav=Decimal("25")),
    ]

    result = build_lots(txns)

    assert len(result.lots) == 2
    assert result.lots[0].source_txn_type == "DIVIDEND_REINVEST"
    assert result.lots[1].source_txn_type == "SWITCH_IN"


def test_reversal_consumes_lots_like_a_normal_outflow() -> None:
    """Real-data-verified: REVERSAL rows carry a real amount+nav and negative
    units — they behave exactly like a redemption, no special-casing needed."""
    txns = [
        _txn("t1", date(2021, 1, 1), "PURCHASE", Decimal("100"), nav=Decimal("50")),
        _txn("t2", date(2021, 1, 5), "REVERSAL", Decimal("-100"), nav=Decimal("50")),
    ]

    result = build_lots(txns)

    assert result.lots[0].units_remaining == Decimal("0")
    assert len(result.realized_gains) == 1
    assert result.realized_gains[0].realized_gain_paise == 0  # same price in and out


def test_input_order_not_just_date_drives_same_day_fifo_order() -> None:
    """build_lots trusts caller-provided order completely — it never sorts.
    Same-day transactions are only ordered correctly because the caller
    (compute/service.py) queries ORDER BY txn_date, sequence. This test
    proves the allocator actually respects list order for same-day ties,
    which is what makes that upstream sequencing meaningful at all."""
    same_day = date(2021, 4, 1)
    txns = [
        _txn("buy1", same_day, "PURCHASE", Decimal("50"), nav=Decimal("10")),
        _txn("buy2", same_day, "PURCHASE", Decimal("50"), nav=Decimal("20")),
        _txn("sell", same_day, "REDEMPTION", Decimal("-50"), nav=Decimal("30")),
    ]

    result = build_lots(txns)

    # FIFO means the same-day sell must consume buy1 (listed first), not buy2,
    # even though both share the same date.
    assert len(result.realized_gains) == 1
    assert result.realized_gains[0].source_txn_id == "buy1"
    assert result.realized_gains[0].cost_per_unit == Decimal("10")


def test_segregation_with_no_amount_or_nav_opens_a_zero_cost_flagged_lot() -> None:
    """Real-data-verified: SEGREGATION rows carry no amount/nav at all (0/5
    real rows had either) — a structural side-pocket event, not a priced
    transaction. Cost-basis carryover is a tax-engine (P1.2) concern; here
    the lot just needs an honest zero cost basis and a flag, not a guess."""
    txns = [_txn("t1", date(2021, 6, 1), "SEGREGATION", Decimal("25"), nav=None, amount_paise=None)]

    result = build_lots(txns)

    assert len(result.lots) == 1
    assert result.lots[0].cost_per_unit == Decimal("0")
    assert result.lots[0].zero_cost_flag is True


def test_tax_lines_and_dividend_payout_do_not_touch_the_lot_ledger() -> None:
    txns = [
        _txn("t1", date(2021, 1, 1), "PURCHASE", Decimal("100"), nav=Decimal("50")),
        _txn("t2", date(2021, 1, 1), "STAMP_DUTY_TAX", None, nav=None, amount_paise=250),
        _txn("t3", date(2021, 1, 1), "STT_TAX", None, nav=None, amount_paise=100),
        _txn("t4", date(2021, 6, 1), "DIVIDEND_PAYOUT", Decimal("0"), nav=Decimal("55")),
    ]

    result = build_lots(txns)

    assert len(result.lots) == 1  # only the PURCHASE opened a lot
    assert result.lots[0].units_remaining == Decimal("100")


def test_amount_fallback_used_when_nav_is_absent_but_amount_present() -> None:
    txns = [
        _txn("t1", date(2021, 1, 1), "PURCHASE", Decimal("100"), nav=None, amount_paise=500_000)
    ]

    result = build_lots(txns)

    assert result.lots[0].cost_per_unit == Decimal("50")
    assert result.lots[0].zero_cost_flag is False


def test_redemption_exceeding_open_lots_raises_insufficient_lots_error() -> None:
    txns = [
        _txn("t1", date(2021, 1, 1), "PURCHASE", Decimal("50"), nav=Decimal("10")),
        _txn("t2", date(2021, 2, 1), "REDEMPTION", Decimal("-80"), nav=Decimal("15")),
    ]

    with pytest.raises(InsufficientLotsError) as exc_info:
        build_lots(txns)

    assert exc_info.value.txn_id == "t2"
    assert exc_info.value.shortfall == Decimal("30")


def test_empty_transaction_list_produces_empty_result() -> None:
    result = build_lots([])
    assert result.lots == []
    assert result.realized_gains == []
