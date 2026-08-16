from fastapi.testclient import TestClient


def test_ingest_csv_endpoint_round_trips(client: TestClient) -> None:
    csv_content = (
        "folio,amc,scheme_name,txn_type,txn_date,amount,units,isin\n"
        "F1,Test AMC,Feeder Fund,PURCHASE,2021-04-15,5000.00,100.5,INF999\n"
    )

    response = client.post(
        "/ingest/csv",
        files={"file": ("transactions.csv", csv_content, "text/csv")},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["inserted_count"] == 1
    assert body["duplicate_count"] == 0
    assert body["coverage_gaps"] == []


def test_ingest_csv_endpoint_rejects_invalid_csv(client: TestClient) -> None:
    bad_csv = "folio,amc\nF1,AMC\n"

    response = client.post(
        "/ingest/csv",
        files={"file": ("transactions.csv", bad_csv, "text/csv")},
    )

    assert response.status_code == 422
    assert "missing required columns" in response.json()["detail"]


def test_ingest_csv_endpoint_byte_identical_reupload_is_a_noop(client: TestClient) -> None:
    csv_content = (
        "folio,amc,scheme_name,txn_type,txn_date,amount,units,isin\n"
        "F1,Test AMC,Feeder Fund,PURCHASE,2021-04-15,5000.00,100.5,INF999\n"
    )

    first = client.post("/ingest/csv", files={"file": ("t.csv", csv_content, "text/csv")})
    second = client.post("/ingest/csv", files={"file": ("t.csv", csv_content, "text/csv")})

    assert first.json()["upload_id"] == second.json()["upload_id"]
    assert second.json()["inserted_count"] == 0
    assert second.json()["duplicate_count"] == 0


def test_ingest_csv_endpoint_overlapping_but_different_content_deduplicates(
    client: TestClient,
) -> None:
    first_csv = (
        "folio,amc,scheme_name,txn_type,txn_date,amount,units,isin\n"
        "F1,Test AMC,Feeder Fund,PURCHASE,2021-04-15,5000.00,100.5,INF999\n"
    )
    second_csv = (
        "folio,amc,scheme_name,txn_type,txn_date,amount,units,isin\n"
        "F1,Test AMC,Feeder Fund,PURCHASE,2021-04-15,5000.00,100.5,INF999\n"
        "F1,Test AMC,Feeder Fund,PURCHASE,2021-05-15,3000.00,60.2,INF999\n"
    )

    client.post("/ingest/csv", files={"file": ("t1.csv", first_csv, "text/csv")})
    response = client.post("/ingest/csv", files={"file": ("t2.csv", second_csv, "text/csv")})

    # Different file content (a genuinely newer export), but the first row
    # overlaps the prior upload — natural-key dedupe must still catch it.
    assert response.json()["inserted_count"] == 1
    assert response.json()["duplicate_count"] == 1
