from fastapi.testclient import TestClient


def test_data_quality_summary_empty_when_no_data(client: TestClient) -> None:
    response = client.get("/data-quality/summary")

    assert response.status_code == 200
    body = response.json()
    assert body["folios"] == []
    assert body["coverage_gaps"] == []
    assert body["unmatched_schemes"] == []
    assert body["failed_xirr_positions"] == []
    assert body["data_freshness"] == []


def test_data_quality_summary_after_ingest_reflects_folio_and_gap(client: TestClient) -> None:
    csv_content = (
        "folio,amc,scheme_name,txn_type,txn_date,amount,units,isin,amfi_code\n"
        "F1,Test AMC,Feeder Fund,PURCHASE,2022-04-15,5000.00,100,INF999,119551\n"
    )
    client.post("/ingest/csv", files={"file": ("t.csv", csv_content, "text/csv")})
    client.post("/portfolio/rebuild-lots")

    response = client.get("/data-quality/summary")
    body = response.json()

    assert len(body["folios"]) == 1
    assert body["folios"][0]["folio"] == "F1"
    assert body["folios"][0]["scheme_count"] == 1
    # CSV import never computes coverage gaps.
    assert body["coverage_gaps"] == []
    # Every field was supplied, so nothing is unmatched.
    assert body["unmatched_schemes"] == []
