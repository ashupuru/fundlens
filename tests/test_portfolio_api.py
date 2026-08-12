from decimal import Decimal

from fastapi.testclient import TestClient


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
