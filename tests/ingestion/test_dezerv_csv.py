from datetime import date

import pytest

from fundlens.ingestion.dezerv_csv import DezervFormatError, parse_dezerv_csv

_HEADER = (
    "Code,Name,Bank Account,Bank Name,Transaction Description,Tran Date,Set Date,"
    "Tran Account,Symbol Code,Security,Buy/Sell Amount,Income,Expenses,Dep/With,"
    "Balance,Custodian Account,Tran Ref.,Desc/Notes,Account Code "
)


def _sample(rows: str) -> str:
    preamble = (
        "Dezerv Investments Private Limited,,,,,,,,,,,,,,,,,,\n"
        '"Unit No. 301, Trade Centre,\n'
        "Bandra East,\n"
        'Mumbai - 400051",,,,,,,,,,,,,,,,,,\n'
        "BANK BOOK,,,,,,,,,,,,,,,,,,\n"
        "From 14/06/2023 To 12/08/2026,,,,,,,,,,,,,,,,,,\n"
        "dezerv. Equity Revival Strategy,,,,,,,,,,,,,,,,,,\n"
    )
    return preamble + _HEADER + "\n" + rows


def test_locates_header_past_the_multiline_quoted_address_preamble() -> None:
    content = _sample(
        "1,Test,ICICI-1,ICICI Bank,Buy,14/06/2023,14/06/2023,MFDEMAT,ABC,Fund A Direct-G,"
        '"-1,000.00",0.00,0.00,0.00,"1,000.00",NSDL-1,,,1\n'
    )
    result = parse_dezerv_csv(content)
    assert len(result.rows) == 1


def test_filters_out_non_mfdemat_rows() -> None:
    content = _sample(
        "1,Test,ICICI-1,ICICI Bank,Cash Deposits,14/06/2023,14/06/2023,DIRECT,CASH,"
        'Cash - Initial Corpus,0.00,0.00,0.00,"1,000.00","1,000.00",NSDL-1,, ,1\n'
        "1,Test,ICICI-1,ICICI Bank,Buy,14/06/2023,14/06/2023,MFDEMAT,ABC,Fund A Direct-G,"
        '"-1,000.00",0.00,0.00,0.00,"0.00",NSDL-1,,,1\n'
    )
    result = parse_dezerv_csv(content)
    assert len(result.rows) == 1
    assert result.rows[0].security == "Fund A Direct-G"


def test_parses_amount_and_date_correctly() -> None:
    content = _sample(
        "1,Test,ICICI-1,ICICI Bank,Buy,14/06/2023,14/06/2023,MFDEMAT,ABC,Fund A Direct-G,"
        '"-125,000.00",0.00,0.00,0.00,"876,000.00",NSDL-1,,,1\n'
    )
    result = parse_dezerv_csv(content)
    row = result.rows[0]
    assert row.txn_date == date(2023, 6, 14)
    assert row.amount_paise == 12_500_000  # Rs 1,25,000 magnitude, sign-agnostic
    assert row.transaction_description == "Buy"
    assert row.custodian_account == "NSDL-1"
    assert row.symbol_code == "ABC"


def test_sell_transaction_parsed() -> None:
    content = _sample(
        "1,Test,ICICI-1,ICICI Bank,Sell,20/06/2023,20/06/2023,MFDEMAT,ABC,Fund A Direct-G,"
        '"50,000.00",0.00,0.00,0.00,"50,000.00",NSDL-1,,,1\n'
    )
    result = parse_dezerv_csv(content)
    assert result.rows[0].transaction_description == "Sell"
    assert result.rows[0].amount_paise == 5_000_000


def test_unrecognized_transaction_description_flagged_not_guessed() -> None:
    content = _sample(
        "1,Test,ICICI-1,ICICI Bank,Dividend,14/07/2023,14/07/2023,MFDEMAT,ABC,Fund A Direct-G,"
        '100.00,0.00,0.00,0.00,"1,100.00",NSDL-1,,,1\n'
    )
    result = parse_dezerv_csv(content)
    assert result.rows == []
    assert result.unrecognized_transaction_descriptions == {"Dividend"}


def test_missing_header_raises_format_error() -> None:
    with pytest.raises(DezervFormatError):
        parse_dezerv_csv("not,a,dezerv,file\n1,2,3,4\n")


def test_multiple_mfdemat_rows_across_schemes() -> None:
    content = _sample(
        "1,Test,ICICI-1,ICICI Bank,Buy,14/06/2023,14/06/2023,MFDEMAT,ABC,Fund A Direct-G,"
        '"-100,000.00",0.00,0.00,0.00,"900,000.00",NSDL-1,,,1\n'
        "1,Test,ICICI-1,ICICI Bank,Buy,15/06/2023,15/06/2023,MFDEMAT,XYZ,Fund B Direct-G,"
        '"-50,000.00",0.00,0.00,0.00,"850,000.00",NSDL-1,,,1\n'
    )
    result = parse_dezerv_csv(content)
    assert len(result.rows) == 2
    assert {r.security for r in result.rows} == {"Fund A Direct-G", "Fund B Direct-G"}


def _sell_row(amount: str) -> str:
    return (
        "1,Test,ICICI-1,ICICI Bank,Sell,20/06/2023,20/06/2023,MFDEMAT,ABC,Fund A Direct-G,"
        f'"{amount}",0.00,0.00,0.00,"50,000.00",NSDL-1,,,1\n'
    )
