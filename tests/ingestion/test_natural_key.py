from datetime import date
from decimal import Decimal

from fundlens.ingestion.natural_key import compute_natural_key


def _key(**overrides: object) -> str:
    defaults: dict[str, object] = {
        "folio": "F1",
        "scheme_identifier": "ISIN1",
        "txn_date": date(2021, 4, 15),
        "txn_type": "PURCHASE",
        "amount_paise": 500_000,
        "units": Decimal("100.5"),
    }
    defaults.update(overrides)
    return compute_natural_key(**defaults)  # type: ignore[arg-type]


def test_identical_inputs_produce_identical_key() -> None:
    assert _key() == _key()


def test_units_trailing_zero_variation_produces_same_key() -> None:
    assert _key(units=Decimal("100.5")) == _key(units=Decimal("100.5000"))


def test_different_folio_produces_different_key() -> None:
    assert _key(folio="F1") != _key(folio="F2")


def test_different_scheme_identifier_produces_different_key() -> None:
    assert _key(scheme_identifier="ISIN1") != _key(scheme_identifier="ISIN2")


def test_different_amount_produces_different_key() -> None:
    assert _key(amount_paise=500_000) != _key(amount_paise=500_001)


def test_different_txn_type_produces_different_key() -> None:
    assert _key(txn_type="PURCHASE") != _key(txn_type="REDEMPTION")


def test_none_amount_and_units_do_not_collide_with_zero() -> None:
    assert _key(amount_paise=None, units=None) != _key(amount_paise=0, units=Decimal("0"))


def test_different_balance_after_produces_different_key() -> None:
    """Real-data regression: two distinct same-day, same-amount, same-unit
    transactions (e.g. two SIP top-ups landing on the same date) must not
    collide — casparser's running balance differs even when everything else
    matches, since it reflects cumulative ledger position."""
    assert _key(balance_after=Decimal("100.5")) != _key(balance_after=Decimal("250.0"))


def test_none_balance_after_does_not_collide_with_zero() -> None:
    assert _key(balance_after=None) != _key(balance_after=Decimal("0"))


def test_different_ordinal_produces_different_key() -> None:
    """Last-resort tiebreaker for rows tying on every other field (e.g. two
    identical-amount stamp-duty-tax lines on the same day, which carry no
    units or balance)."""
    assert _key(ordinal=0) != _key(ordinal=1)


def test_ordinal_defaults_to_zero() -> None:
    assert _key() == _key(ordinal=0)
