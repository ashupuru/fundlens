from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from fundlens.market_data import groww_service

_GROWW_PAYLOAD = {
    "content": [
        {
            "scheme_code": "111",
            "scheme_name": "Acme Small Cap Fund Direct Growth",
            "fund_house": "Acme Mutual Fund",
            "category": "Equity",
            "sub_category": "Small Cap",
            "plan_type": "Direct",
            "aum": 10000.0,
            "groww_rating": 4,
            "risk_rating": 6,
            "expense_ratio": "0.5",
            "fund_manager": "Test Manager",
            "exit_load": "1% if redeemed within 1 year.",
            "return1y": 20.0,
            "return3y": 25.0,
            "return5y": 18.0,
            "return10y": None,
            "sip_return1y": 22.0,
            "sip_return3y": 24.0,
            "sip_return5y": 19.0,
            "launch_date": "2015-01-01",
        },
        {
            "scheme_code": "222",
            "scheme_name": "Acme Small Cap Fund Direct Growth (Old Name)",
            "fund_house": "Acme Mutual Fund",
            "category": "Equity",
            "sub_category": "Small Cap",
            "plan_type": "Direct",
            "aum": 50000.0,
            "groww_rating": 5,
            "risk_rating": 6,
            "expense_ratio": "0.4",
            "fund_manager": "Other Manager",
            "exit_load": "1% if redeemed within 1 year.",
            "return1y": 22.0,
            "return3y": 28.0,
            "return5y": 20.0,
            "return10y": None,
            "sip_return1y": 23.0,
            "sip_return3y": 25.0,
            "sip_return5y": 21.0,
            "launch_date": "2012-01-01",
        },
        {
            "scheme_code": "333",
            "scheme_name": "Acme Large Cap Fund Direct Growth",
            "fund_house": "Acme Mutual Fund",
            "category": "Equity",
            "sub_category": "Large Cap",
            "plan_type": "Direct",
            "aum": 90000.0,
            "groww_rating": 5,
            "risk_rating": 4,
            "expense_ratio": "0.3",
            "fund_manager": "Third Manager",
            "exit_load": "None",
            "return1y": 15.0,
            "return3y": 18.0,
            "return5y": 14.0,
            "return10y": 12.0,
            "sip_return1y": 16.0,
            "sip_return3y": 17.0,
            "sip_return5y": 15.0,
            "launch_date": "2010-01-01",
        },
    ],
    "total_results": 3,
}


@pytest.fixture(autouse=True)
def _patch_groww_fetch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(groww_service, "fetch_groww_screener", lambda: _GROWW_PAYLOAD)


def _seed_groww_metadata(client: TestClient) -> None:
    response = client.post("/market-data/refresh-groww")
    assert response.status_code == 200
    assert response.json()["ok"] is True


def _ingest_holding(client: TestClient, amfi_code: str, scheme_name: str) -> None:
    csv_content = (
        "folio,amc,scheme_name,txn_type,txn_date,amount,units,amfi_code\n"
        f"F1,Acme Mutual Fund,{scheme_name},PURCHASE,2021-04-15,5000.00,100.5,{amfi_code}\n"
    )
    response = client.post("/ingest/csv", files={"file": ("t.csv", csv_content, "text/csv")})
    assert response.status_code == 200
    client.post("/portfolio/rebuild-lots")


def test_fund_detail_for_held_fund_with_groww_metadata(client: TestClient) -> None:
    _seed_groww_metadata(client)
    _ingest_holding(client, "111", "Acme Small Cap Fund Direct Growth")

    response = client.get("/funds/111")

    assert response.status_code == 200
    body = response.json()
    assert body["metadata_available"] is True
    assert body["scheme_name"] == "Acme Small Cap Fund Direct Growth"
    assert body["fund_house"] == "Acme Mutual Fund"
    assert body["sub_category"] == "Small Cap"
    assert body["groww_rating"] == 4
    assert body["aum"] == "10000.0000"

    assert body["your_position"] is not None
    assert body["your_position"]["invested_paise"] == 500_000
    assert len(body["holdings"]) == 1
    assert body["holdings"][0]["folio"] == "F1"

    similar_codes = [f["amfi_code"] for f in body["similar_funds"]]
    assert "222" in similar_codes  # same sub_category
    assert "333" not in similar_codes  # different sub_category (Large Cap)
    assert "111" not in similar_codes  # never includes itself


def test_similar_funds_ordered_by_rating_then_aum(client: TestClient) -> None:
    _seed_groww_metadata(client)

    response = client.get("/funds/111")

    similar = response.json()["similar_funds"]
    assert similar[0]["amfi_code"] == "222"  # rating 5 beats 111's own 4
    assert similar[0]["groww_rating"] == 5


def test_fund_detail_for_held_fund_with_no_groww_row(client: TestClient) -> None:
    _seed_groww_metadata(client)
    _ingest_holding(client, "999", "Totally Unlisted Fund")

    response = client.get("/funds/999")

    assert response.status_code == 200
    body = response.json()
    assert body["metadata_available"] is False
    assert body["scheme_name"] == "Totally Unlisted Fund"  # falls back to the holding's own name
    assert body["fund_house"] is None
    assert body["aum"] is None
    assert body["groww_rating"] is None
    assert body["your_position"] is not None
    assert body["similar_funds"] == []


def test_fund_detail_for_unheld_groww_known_fund(client: TestClient) -> None:
    _seed_groww_metadata(client)

    response = client.get("/funds/222")

    assert response.status_code == 200
    body = response.json()
    assert body["metadata_available"] is True
    assert body["scheme_name"] == "Acme Small Cap Fund Direct Growth (Old Name)"
    assert body["your_position"] is None
    assert body["holdings"] == []


def test_fund_detail_for_completely_unknown_code(client: TestClient) -> None:
    response = client.get("/funds/does-not-exist")

    assert response.status_code == 200
    body = response.json()
    assert body["metadata_available"] is False
    assert body["scheme_name"] is None
    assert body["your_position"] is None
    assert body["holdings"] == []
    assert body["similar_funds"] == []
    assert body["rolling_returns"] == []


def test_fund_detail_rolling_returns_empty_without_scheme_metadata(client: TestClient) -> None:
    _seed_groww_metadata(client)
    _ingest_holding(client, "111", "Acme Small Cap Fund Direct Growth")

    response = client.get("/funds/111")

    # No SchemeMetadata row seeded in this test's DB -- category/tax_asset_class
    # are unknown, so rolling returns can't be computed. Flagged as empty, not
    # fabricated with a guessed category.
    assert response.json()["rolling_returns"] == []


def test_fund_detail_rolling_returns_populated_with_scheme_metadata() -> None:
    """SchemeMetadata/NavHistory/BenchmarkHistory have no ingest endpoint to
    seed them through, so this test builds its own client + shared in-memory
    DB (same construction as the `client` fixture in conftest.py) to seed
    them directly alongside using the real API surface."""
    from collections.abc import Iterator

    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    from sqlalchemy.pool import StaticPool

    from fundlens.api.deps import get_session
    from fundlens.api.main import app
    from fundlens.db.models import (
        Base,
        BenchmarkHistory,
        BenchmarkId,
        NavHistory,
        NavSource,
        SchemeMetadata,
    )
    from fundlens.tax.asset_class import TaxAssetClass

    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)

    def override_get_session() -> Iterator[Session]:
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = override_get_session
    try:
        with Session(engine) as seed_session:
            seed_session.add(
                SchemeMetadata(
                    amfi_code="111",
                    category_raw="Open Ended Schemes(Equity Scheme - Small Cap Fund)",
                    tax_asset_class=TaxAssetClass.EQUITY,
                )
            )
            seed_session.add(
                NavHistory(
                    amfi_code="111", nav_date=date(2020, 1, 1), nav=Decimal("100"),
                    source=NavSource.MANUAL,
                )
            )
            seed_session.add(
                NavHistory(
                    amfi_code="111", nav_date=date(2021, 6, 1), nav=Decimal("130"),
                    source=NavSource.MANUAL,
                )
            )
            seed_session.add(
                BenchmarkHistory(
                    benchmark_id=BenchmarkId.NIFTY_SMALLCAP250_TRI, nav_date=date(2020, 1, 1),
                    value=Decimal("100"), proxy=True, proxy_note="test",
                )
            )
            seed_session.add(
                BenchmarkHistory(
                    benchmark_id=BenchmarkId.NIFTY_SMALLCAP250_TRI, nav_date=date(2021, 6, 1),
                    value=Decimal("115"), proxy=True, proxy_note="test",
                )
            )
            seed_session.commit()

        with TestClient(app) as local_client:
            _seed_groww_metadata(local_client)
            _ingest_holding(local_client, "111", "Acme Small Cap Fund Direct Growth")
            response = local_client.get("/funds/111")
    finally:
        app.dependency_overrides.clear()
        engine.dispose()

    body = response.json()
    assert len(body["rolling_returns"]) == 3
    window_1y = next(w for w in body["rolling_returns"] if w["years"] == 1)
    assert window_1y["sample_count"] > 0
    assert window_1y["mean_alpha_pct"] is not None
