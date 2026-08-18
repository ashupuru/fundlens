from fastapi.testclient import TestClient


def test_capital_gains_with_no_data_returns_empty_report(client: TestClient) -> None:
    response = client.get("/reports/capital-gains", params={"fy": "2024-25"})

    assert response.status_code == 200
    body = response.json()
    assert body["financial_year"] == "2024-25"
    assert body["lines"] == []
    assert body["total_stcg_paise"] == 0


def test_capital_gains_invalid_fy_returns_422(client: TestClient) -> None:
    response = client.get("/reports/capital-gains", params={"fy": "not-a-year"})
    assert response.status_code == 422


def test_capital_gains_missing_fy_param_returns_422(client: TestClient) -> None:
    response = client.get("/reports/capital-gains")
    assert response.status_code == 422


def test_capital_gains_csv_with_no_data_returns_header_only(client: TestClient) -> None:
    response = client.get("/reports/capital-gains.csv", params={"fy": "2024-25"})

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert "attachment" in response.headers["content-disposition"]
    assert "Scheme Name" in response.text


def test_capital_gains_round_trip_after_ingest_and_redemption(client: TestClient) -> None:
    csv_content = (
        "folio,amc,scheme_name,txn_type,txn_date,amount,units,isin,amfi_code\n"
        "F1,Test AMC,Feeder Fund,PURCHASE,2022-04-15,5000.00,100,INF999,119551\n"
        "F1,Test AMC,Feeder Fund,REDEMPTION,2024-05-01,-8000.00,-100,INF999,119551\n"
    )
    client.post("/ingest/csv", files={"file": ("t.csv", csv_content, "text/csv")})
    client.post("/portfolio/rebuild-lots")

    response = client.get("/reports/capital-gains", params={"fy": "2024-25"})
    body = response.json()

    assert len(body["lines"]) == 1
    line = body["lines"][0]
    # No scheme_metadata was ever fetched in this test's DB, so the gain
    # should be honestly unclassified, not silently guessed.
    assert line["gain_type"] == "unclassified"
    assert line["tax_asset_class"] is None
    assert body["schemes_missing_metadata"] == 1


def test_yearly_report_json_with_no_data_returns_empty_report(client: TestClient) -> None:
    response = client.get("/reports/yearly.json", params={"fy": "2023-24"})

    assert response.status_code == 200
    body = response.json()
    assert body["financial_year"] == "2023-24"
    assert body["opening_value_paise"] == 0
    assert body["closing_value_paise"] == 0
    assert body["best_funds"] == []
    assert body["allocation_drift"] is None


def test_yearly_report_json_invalid_fy_returns_422(client: TestClient) -> None:
    response = client.get("/reports/yearly.json", params={"fy": "not-a-year"})
    assert response.status_code == 422


def test_yearly_report_pdf_with_no_data(client: TestClient) -> None:
    response = client.get("/reports/yearly", params={"fy": "2023-24"})

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert "attachment" in response.headers["content-disposition"]
    assert response.content[:4] == b"%PDF"


def test_yearly_report_json_after_ingest(client: TestClient) -> None:
    csv_content = (
        "folio,amc,scheme_name,txn_type,txn_date,amount,units,isin,amfi_code\n"
        "F1,Test AMC,Feeder Fund,PURCHASE,2022-04-15,5000.00,100,INF999,119551\n"
    )
    client.post("/ingest/csv", files={"file": ("t.csv", csv_content, "text/csv")})
    client.post("/portfolio/rebuild-lots")

    response = client.get("/reports/yearly.json", params={"fy": "2023-24"})

    assert response.status_code == 200
    body = response.json()
    # No NAV was ever fetched in this test's DB, so the position can't be
    # priced -- opening/closing value should be 0, not fabricated.
    assert body["opening_value_paise"] == 0
    assert body["closing_value_paise"] == 0
