import uuid
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy.orm import Session

from fundlens.compute.valuation_history import (
    _weekly_report_dates,
    compute_portfolio_value_at_dates,
    compute_portfolio_value_history,
)
from fundlens.db.models import (
    NavHistory,
    NavSource,
    RawCASUpload,
    RawTransaction,
    TransactionType,
    UploadStatus,
)


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
    amfi_code: str | None = "111",
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


def _add_nav(session: Session, amfi_code: str, nav_date: date, nav: str) -> None:
    session.add(
        NavHistory(
            amfi_code=amfi_code, nav_date=nav_date, nav=Decimal(nav), source=NavSource.MANUAL
        )
    )


def test_weekly_report_dates_steps_by_seven_and_includes_today() -> None:
    first = date(2024, 1, 1)
    today = date(2024, 1, 22)  # exactly 3 weeks later

    dates = _weekly_report_dates(first, today)

    assert dates == [date(2024, 1, 1), date(2024, 1, 8), date(2024, 1, 15), date(2024, 1, 22)]


def test_weekly_report_dates_always_includes_today_even_off_cadence() -> None:
    first = date(2024, 1, 1)
    today = date(2024, 1, 10)  # not a multiple of 7 days from first

    dates = _weekly_report_dates(first, today)

    assert dates[-1] == today
    assert dates[:-1] == [date(2024, 1, 1), date(2024, 1, 8)]


def test_weekly_report_dates_first_equals_today_is_single_point() -> None:
    d = date(2024, 5, 1)
    assert _weekly_report_dates(d, d) == [d]


def test_compute_portfolio_value_history_empty_portfolio(session: Session) -> None:
    assert compute_portfolio_value_history(session) == []


def test_compute_portfolio_value_history_tracks_units_and_gross_invested(
    session: Session,
) -> None:
    today = date.today()
    first_date = today - timedelta(days=200)
    redemption_date = first_date + timedelta(days=100)

    upload = _make_upload(session)
    _add_txn(
        session, upload.id, txn_type=TransactionType.PURCHASE, txn_date=first_date,
        units=Decimal("100"), amount_paise=1_000_000, sequence=0,
    )
    _add_txn(
        session, upload.id, txn_type=TransactionType.REDEMPTION, txn_date=redemption_date,
        units=Decimal("-40"), amount_paise=-440_000, sequence=1,
    )
    _add_nav(session, "111", first_date, "100")
    _add_nav(session, "111", redemption_date, "110")
    _add_nav(session, "111", today, "120")
    session.commit()

    points = compute_portfolio_value_history(session)

    assert points[0].as_of_date == first_date
    assert points[0].value_paise == 1_000_000  # 100 units * NAV 100
    assert points[0].invested_paise == 1_000_000  # gross invested, PURCHASE only

    assert points[-1].as_of_date == today
    assert points[-1].value_paise == 720_000  # 60 units remaining * NAV 120
    # Gross invested is monotonically non-decreasing -- the redemption does
    # NOT reduce it, matching PortfolioMetrics.invested_paise's established
    # meaning elsewhere in this app.
    assert points[-1].invested_paise == 1_000_000
    assert points[-1].positions_missing_nav == 0


def test_compute_portfolio_value_history_includes_fully_exited_positions(
    session: Session,
) -> None:
    today = date.today()
    first_date = today - timedelta(days=200)
    exit_date = first_date + timedelta(days=50)

    upload = _make_upload(session)
    _add_txn(
        session, upload.id, txn_type=TransactionType.PURCHASE, txn_date=first_date,
        units=Decimal("50"), amount_paise=500_000, sequence=0,
    )
    _add_txn(
        session, upload.id, txn_type=TransactionType.REDEMPTION, txn_date=exit_date,
        units=Decimal("-50"), amount_paise=-550_000, sequence=1,
    )
    _add_nav(session, "111", first_date, "100")
    session.commit()

    points = compute_portfolio_value_history(session)

    # Before the exit: fully priced and held.
    at_purchase = next(p for p in points if p.as_of_date == first_date)
    assert at_purchase.value_paise == 500_000
    assert at_purchase.invested_paise == 500_000

    # After the exit: zero units held, contributes nothing to value, but the
    # gross invested figure survives (it's a cumulative historical fact).
    assert points[-1].as_of_date == today
    assert points[-1].value_paise == 0
    assert points[-1].invested_paise == 500_000
    assert points[-1].positions_missing_nav == 0


def test_compute_portfolio_value_history_flags_missing_nav_without_crashing(
    session: Session,
) -> None:
    today = date.today()
    first_date = today - timedelta(days=30)

    upload = _make_upload(session)
    _add_txn(
        session, upload.id, txn_type=TransactionType.PURCHASE, txn_date=first_date,
        units=Decimal("10"), amount_paise=100_000, sequence=0,
        amfi_code="999",  # deliberately no NavHistory seeded for this code
    )
    session.commit()

    points = compute_portfolio_value_history(session)

    assert points[-1].value_paise == 0
    assert points[-1].positions_missing_nav == 1
    # invested_paise is NAV-independent -- unaffected by missing pricing.
    assert points[-1].invested_paise == 100_000


def test_compute_portfolio_value_history_no_amfi_code_excluded_from_value(
    session: Session,
) -> None:
    today = date.today()
    first_date = today - timedelta(days=30)

    upload = _make_upload(session)
    _add_txn(
        session, upload.id, txn_type=TransactionType.PURCHASE, txn_date=first_date,
        units=Decimal("10"), amount_paise=100_000, sequence=0, amfi_code=None, isin=None,
    )
    session.commit()

    points = compute_portfolio_value_history(session)

    assert points[-1].value_paise == 0
    assert points[-1].positions_missing_nav == 1
    assert points[-1].invested_paise == 100_000


def test_compute_portfolio_value_at_dates_custom_non_weekly_dates(session: Session) -> None:
    """compute_portfolio_value_history's weekly cadence is one caller of the
    shared engine -- confirm it also works with an arbitrary, non-weekly
    date list (e.g. [fy_start, fy_end]), as compute/yearly_report.py needs."""
    day0 = date(2023, 1, 1)
    day1 = date(2023, 6, 15)
    day2 = date(2024, 3, 31)

    upload = _make_upload(session)
    _add_txn(
        session, upload.id, txn_type=TransactionType.PURCHASE, txn_date=day0,
        units=Decimal("100"), amount_paise=1_000_000, sequence=0,
    )
    _add_nav(session, "111", day0, "100")
    _add_nav(session, "111", day1, "110")
    _add_nav(session, "111", day2, "120")
    session.commit()

    points = compute_portfolio_value_at_dates(session, [day0, day2])

    assert [p.as_of_date for p in points] == [day0, day2]
    assert points[0].value_paise == 1_000_000  # 100 units * NAV 100
    assert points[1].value_paise == 1_200_000  # 100 units * NAV 120


def test_compute_portfolio_value_history_real_scale_does_not_crash(session: Session) -> None:
    """~5 years, ~20 positions -- roughly the shape of a real long-held
    portfolio (weekly granularity over 5 years is ~260 report dates), to
    catch any O(positions * report_dates * transactions) blowup before it
    ships, not just correctness on a two-transaction fixture."""
    today = date.today()
    first_date = today - timedelta(days=5 * 365)
    upload = _make_upload(session)

    for i in range(20):
        amfi_code = str(100 + i)
        buy_date = first_date + timedelta(days=i)
        _add_txn(
            session, upload.id, folio="F1", scheme_name=f"Fund {i}", amfi_code=amfi_code,
            isin=f"ISIN{i}", txn_type=TransactionType.PURCHASE, txn_date=buy_date,
            units=Decimal("100"), amount_paise=1_000_000, sequence=0,
        )
        for month in range(0, 5 * 12, 1):
            nav_date = buy_date + timedelta(days=month * 30)
            if nav_date > today:
                break
            _add_nav(session, amfi_code, nav_date, str(100 + month))
    session.commit()

    points = compute_portfolio_value_history(session)

    assert len(points) == len(_weekly_report_dates(first_date, today))
    assert points[0].invested_paise == 1_000_000  # only fund 0 has bought in by day 0
    assert points[-1].invested_paise == 20_000_000  # all 20 positions bought in by "today"
    assert points[-1].value_paise > 0
