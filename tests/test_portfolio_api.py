from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from fundlens.market_data import groww_service

_GROWW_PAYLOAD = {
    "content": [
        {
            "scheme_code": "111",
            "scheme_name": "Acme Small Cap Fund Direct Growth",
            "category": "Equity",
            "sub_category": "Small Cap",
        },
        {
            "scheme_code": "222",
            "scheme_name": "Acme Gold Fund Direct Growth",
            "category": "Commodities",
            "sub_category": "Gold",
        },
    ],
    "total_results": 2,
}


def test_rebuild_lots_with_no_transactions_is_a_clean_noop(client: TestClient) -> None:
    response = client.post("/portfolio/rebuild-lots")

    assert response.status_code == 200
    body = response.json()
    assert body["folios_processed"] == 0
    assert body["lots_created"] == 0


def test_holdings_empty_with_no_transactions(client: TestClient) -> None:
    response = client.get("/portfolio/holdings")

    assert response.status_code == 200
    assert response.json() == []


def test_ingest_then_rebuild_then_view_holdings_round_trip(client: TestClient) -> None:
    csv_content = (
        "folio,amc,scheme_name,txn_type,txn_date,amount,units,isin\n"
        "F1,Test AMC,Feeder Fund,PURCHASE,2021-04-15,5000.00,100.5,INF999\n"
    )
    client.post("/ingest/csv", files={"file": ("t.csv", csv_content, "text/csv")})

    rebuild_response = client.post("/portfolio/rebuild-lots")
    assert rebuild_response.json()["lots_created"] == 1

    holdings_response = client.get("/portfolio/holdings")
    holdings = holdings_response.json()
    assert len(holdings) == 1
    assert holdings[0]["folio"] == "F1"
    assert holdings[0]["scheme_name"] == "Feeder Fund"
    assert Decimal(holdings[0]["units_remaining"]) == Decimal("100.5")

    by_amc_response = client.get("/portfolio/holdings/by-amc")
    by_amc = by_amc_response.json()
    assert len(by_amc) == 1
    assert by_amc[0]["amc"] == "Test AMC"
    assert by_amc[0]["scheme_count"] == 1


def test_metrics_empty_with_no_transactions(client: TestClient) -> None:
    assert client.get("/portfolio/metrics").json() == []

    portfolio = client.get("/portfolio/metrics/portfolio").json()
    assert portfolio["xirr"] is None
    assert portfolio["xirr_reason"] == "insufficient_flow_variation"


def test_metrics_round_trip_without_nav_reports_unavailable(client: TestClient) -> None:
    csv_content = (
        "folio,amc,scheme_name,txn_type,txn_date,amount,units,isin\n"
        "F1,Test AMC,Feeder Fund,PURCHASE,2021-04-15,5000.00,100.5,INF999\n"
    )
    client.post("/ingest/csv", files={"file": ("t.csv", csv_content, "text/csv")})
    client.post("/portfolio/rebuild-lots")

    response = client.get("/portfolio/metrics")
    body = response.json()

    assert len(body) == 1
    m = body[0]
    assert m["invested_paise"] == 500_000
    # No amfi_code in this CSV row, so no NAV lookup is possible.
    assert m["current_value_paise"] is None
    assert m["xirr"] is None
    assert m["xirr_reason"] == "nav_unavailable"


def test_breakdown_empty_with_no_holdings(client: TestClient) -> None:
    response = client.get("/portfolio/breakdown")

    assert response.status_code == 200
    assert response.json() == []


def test_breakdown_groups_by_category_and_sub_category(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(groww_service, "fetch_groww_screener", lambda: _GROWW_PAYLOAD)
    refresh_response = client.post("/market-data/refresh-groww")
    assert refresh_response.json()["ok"] is True

    csv_content = (
        "folio,amc,scheme_name,txn_type,txn_date,amount,units,amfi_code\n"
        "F1,Acme Mutual Fund,Acme Small Cap Fund,PURCHASE,2021-04-15,5000.00,100,111\n"
        "F1,Acme Mutual Fund,Acme Gold Fund,PURCHASE,2021-04-15,5000.00,50,222\n"
    )
    client.post("/ingest/csv", files={"file": ("t.csv", csv_content, "text/csv")})
    client.post("/portfolio/rebuild-lots")

    by_category = client.get("/portfolio/breakdown?level=category").json()
    labels = {g["label"] for g in by_category}
    assert labels == {"Equity", "Commodities"}
    total_pct = sum(g["allocation_pct"] for g in by_category)
    assert total_pct == pytest.approx(0.0)  # no NAV backfilled -- both positions unvalued

    by_sub_category = client.get("/portfolio/breakdown?level=sub_category").json()
    sub_labels = {g["label"] for g in by_sub_category}
    assert sub_labels == {"Small Cap", "Gold"}


def test_breakdown_defaults_to_category_level(client: TestClient) -> None:
    response = client.get("/portfolio/breakdown")

    assert response.status_code == 200  # no query param needed, defaults cleanly
