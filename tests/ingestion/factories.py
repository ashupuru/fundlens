"""Builders for real casparser types, used across ingestion tests.

Using the library's actual Pydantic models (not hand-rolled stand-ins) means
these tests exercise the real shape casparser produces.
"""

from datetime import date
from decimal import Decimal

from casparser.types import (
    CASData,
    CASFileType,
    FileType,
    Folio,
    InvestorInfo,
    Scheme,
    SchemeValuation,
    StatementPeriod,
    TransactionData,
    TransactionType,
)


def make_transaction(
    *,
    txn_date: date = date(2021, 4, 15),
    description: str = "Purchase",
    amount: Decimal | None = Decimal("5000.00"),
    units: Decimal | None = Decimal("100.5000"),
    nav: Decimal | None = Decimal("49.75"),
    balance: Decimal | None = Decimal("100.5000"),
    type: TransactionType = TransactionType.PURCHASE,
    dividend_rate: Decimal | None = None,
    gift_folio: str | None = None,
) -> TransactionData:
    return TransactionData(
        date=txn_date,
        description=description,
        amount=amount,
        units=units,
        nav=nav,
        balance=balance,
        type=type,
        dividend_rate=dividend_rate,
        gift_folio=gift_folio,
    )


def make_scheme(
    *,
    scheme_name: str = "Test Fund - Direct - Growth",
    isin: str | None = "INF000X01234",
    amfi: str | None = "123456",
    open_balance: Decimal = Decimal("0"),
    transactions: list[TransactionData] | None = None,
) -> Scheme:
    txns = transactions if transactions is not None else [make_transaction()]
    close = txns[-1].balance if txns and txns[-1].balance is not None else Decimal("0")
    return Scheme(
        scheme=scheme_name,
        rta_code="TEST001",
        rta="CAMS",
        isin=isin,
        amfi=amfi,
        open=open_balance,
        close=close,
        close_calculated=close,
        valuation=SchemeValuation(date=date(2021, 4, 30), nav=Decimal("49.75"), value=Decimal("0")),
        transactions=txns,
    )


def make_folio(
    *,
    folio: str = "12345678",
    amc: str = "Test AMC",
    pan: str | None = "ABCDE1234F",
    schemes: list[Scheme] | None = None,
) -> Folio:
    return Folio(
        folio=folio,
        amc=amc,
        PAN=pan,
        schemes=schemes if schemes is not None else [make_scheme()],
    )


def make_cas_data(
    *,
    folios: list[Folio] | None = None,
    period_from: str = "2021-04-01",
    period_to: str = "2021-04-30",
) -> CASData:
    return CASData(
        statement_period=StatementPeriod(from_=period_from, to=period_to),
        folios=folios if folios is not None else [make_folio()],
        investor_info=InvestorInfo(name="Test Investor", email="", address="", mobile=""),
        cas_type=CASFileType.DETAILED,
        file_type=FileType.CAMS,
    )
