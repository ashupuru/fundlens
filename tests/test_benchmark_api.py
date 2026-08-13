from fastapi.testclient import TestClient


def test_benchmark_comparison_empty_when_no_data(client: TestClient) -> None:
    response = client.get("/benchmarks/comparison")
    assert response.status_code == 200
    assert response.json() == []


def test_portfolio_benchmark_comparison_defaults_to_nifty50(client: TestClient) -> None:
    response = client.get("/benchmarks/comparison/portfolio")
    assert response.status_code == 200
    body = response.json()
    assert body["benchmark_id"] == "NIFTY50_TRI"
    assert body["portfolio_xirr"] is None


def test_portfolio_benchmark_comparison_rejects_unknown_benchmark_id(client: TestClient) -> None:
    response = client.get("/benchmarks/comparison/portfolio", params={"benchmark_id": "NOT_REAL"})
    assert response.status_code == 422


def test_benchmark_comparison_after_ingest_reflects_category_default(client: TestClient) -> None:
    csv_content = (
        "folio,amc,scheme_name,txn_type,txn_date,amount,units,isin,amfi_code\n"
        "F1,Test AMC,Feeder Fund,PURCHASE,2022-04-15,5000.00,100,INF999,119551\n"
    )
    client.post("/ingest/csv", files={"file": ("t.csv", csv_content, "text/csv")})
    client.post("/portfolio/rebuild-lots")

    response = client.get("/benchmarks/comparison")
    body = response.json()

    assert len(body) == 1
    # No scheme_metadata was ever fetched in this test's DB, so the benchmark
    # should be honestly unresolved, not silently guessed.
    assert body[0]["benchmark_id"] is None
    assert body[0]["benchmark_xirr_reason"] == "no_category_benchmark"
