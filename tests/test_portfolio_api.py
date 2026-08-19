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


def test_breakdown_amc_level_groups_by_amc(client: TestClient) -> None:
    csv_content = (
        "folio,amc,scheme_name,txn_type,txn_date,amount,units,amfi_code\n"
        "F1,AMC One,Fund A,PURCHASE,2021-04-15,5000.00,100,111\n"
        "F1,AMC Two,Fund B,PURCHASE,2021-04-15,5000.00,50,222\n"
    )
    client.post("/ingest/csv", files={"file": ("t.csv", csv_content, "text/csv")})
    client.post("/portfolio/rebuild-lots")

    by_amc = client.get("/portfolio/breakdown?level=amc").json()

    assert {g["label"] for g in by_amc} == {"AMC One", "AMC Two"}
    assert all(g["fund_count"] == 1 for g in by_amc)


def test_value_history_empty_with_no_transactions(client: TestClient) -> None:
    response = client.get("/portfolio/value-history")

    assert response.status_code == 200
    assert response.json() == []


def test_value_history_round_trip_after_ingest(client: TestClient) -> None:
    csv_content = (
        "folio,amc,scheme_name,txn_type,txn_date,amount,units,amfi_code\n"
        "F1,Test AMC,Feeder Fund,PURCHASE,2021-04-15,5000.00,100,111\n"
    )
    client.post("/ingest/csv", files={"file": ("t.csv", csv_content, "text/csv")})
    client.post("/portfolio/rebuild-lots")

    response = client.get("/portfolio/value-history")

    assert response.status_code == 200
    points = response.json()
    assert len(points) > 0
    assert points[0]["as_of_date"] == "2021-04-15"
    assert points[0]["invested_paise"] == 500_000
    # No NAV backfilled in this test, so value stays unavailable throughout.
    assert points[-1]["value_paise"] == 0
    assert points[-1]["positions_missing_nav"] == 1


def _ingest_one_holding(client: TestClient) -> tuple[str, str]:
    csv_content = (
        "folio,amc,scheme_name,txn_type,txn_date,amount,units,isin,amfi_code\n"
        "F1,Test AMC,Acme Fund,PURCHASE,2021-04-15,5000.00,100.5,INF999,111\n"
    )
    client.post("/ingest/csv", files={"file": ("t.csv", csv_content, "text/csv")})
    client.post("/portfolio/rebuild-lots")
    holding = client.get("/portfolio/holdings").json()[0]
    return holding["folio"], holding["scheme_key"]


def test_holding_source_defaults_to_the_derived_value(client: TestClient) -> None:
    _ingest_one_holding(client)

    metrics = client.get("/portfolio/metrics").json()[0]

    assert metrics["source"] == "Manual CSV"  # csv-import-v1
    assert metrics["source_is_override"] is False


def test_holding_source_can_be_overridden_and_cleared(client: TestClient) -> None:
    folio, scheme_key = _ingest_one_holding(client)

    set_response = client.put(
        "/portfolio/holding-source",
        json={"folio": folio, "scheme_key": scheme_key, "source": "Groww"},
    )
    assert set_response.status_code == 200
    assert set_response.json()["source"] == "Groww"
    assert set_response.json()["source_is_override"] is True

    metrics = client.get("/portfolio/metrics").json()[0]
    assert metrics["source"] == "Groww"
    assert metrics["source_is_override"] is True

    # Clearing falls back to the derived attribution rather than to a blank.
    clear_response = client.put(
        "/portfolio/holding-source",
        json={"folio": folio, "scheme_key": scheme_key, "source": None},
    )
    assert clear_response.json()["source"] == "Manual CSV"
    assert clear_response.json()["source_is_override"] is False
    assert client.get("/portfolio/metrics").json()[0]["source"] == "Manual CSV"


def test_blank_source_clears_rather_than_storing_an_empty_label(client: TestClient) -> None:
    folio, scheme_key = _ingest_one_holding(client)
    client.put(
        "/portfolio/holding-source",
        json={"folio": folio, "scheme_key": scheme_key, "source": "Groww"},
    )

    response = client.put(
        "/portfolio/holding-source",
        json={"folio": folio, "scheme_key": scheme_key, "source": "   "},
    )

    assert response.json()["source_is_override"] is False
    assert response.json()["source"] == "Manual CSV"


def test_overriding_twice_updates_rather_than_duplicating(client: TestClient) -> None:
    folio, scheme_key = _ingest_one_holding(client)
    for label in ("Groww", "Kuvera"):
        client.put(
            "/portfolio/holding-source",
            json={"folio": folio, "scheme_key": scheme_key, "source": label},
        )

    assert client.get("/portfolio/metrics").json()[0]["source"] == "Kuvera"


def test_known_sources_include_builtins_and_user_labels(client: TestClient) -> None:
    folio, scheme_key = _ingest_one_holding(client)
    client.put(
        "/portfolio/holding-source",
        json={"folio": folio, "scheme_key": scheme_key, "source": "Groww"},
    )

    sources = client.get("/portfolio/holding-sources").json()["sources"]

    assert "Groww" in sources  # what the user typed
    assert "Direct / AMC folio" in sources  # built-in suggestion
    assert "Zerodha" in sources


def _seed_cross_source_duplicate(client: TestClient) -> None:
    """A CAS-style purchase and a broker-style purchase of the same trade:
    identical units, amounts a few paise apart -- the real shape of the bug."""
    cas = (
        "folio,amc,scheme_name,txn_type,txn_date,amount,units,isin,amfi_code\n"
        "AMC-FOLIO-1,ICICI,Acme Hybrid,PURCHASE,2021-10-06,49997.50,379.9850,INF001,120700\n"
    )
    client.post("/ingest/csv", files={"file": ("cas.csv", cas, "text/csv")})
    client.post("/portfolio/rebuild-lots")
    # Only the broker side is re-attributed; the CAS side keeps its derived
    # source, as a real CAS position would (no override until a user sets one).
    broker = (
        "folio,amc,scheme_name,txn_type,txn_date,amount,units,isin,amfi_code\n"
        "ZERODHA,ICICI,Acme Hybrid,PURCHASE,2021-10-06,49997.44,379.9850,INF001,120700\n"
    )
    client.post("/ingest/csv", files={"file": ("z.csv", broker, "text/csv")})
    client.post("/portfolio/rebuild-lots")
    zerodha = [h for h in client.get("/portfolio/holdings").json() if h["folio"] == "ZERODHA"][0]
    client.put(
        "/portfolio/holding-source",
        json={
            "folio": "ZERODHA",
            "scheme_key": zerodha["scheme_key"],
            "source": "Zerodha",
        },
    )


def test_duplicate_preview_finds_the_pair_without_deleting(client: TestClient) -> None:
    _seed_cross_source_duplicate(client)

    body = client.get("/portfolio/duplicates/preview").json()

    assert body["removable_count"] == 1
    dup = body["duplicates"][0]
    assert dup["units"] == "379.9850"
    assert {r["source"] for r in dup["rows"]} == {"Manual CSV", "Zerodha"}
    # The broker side is the one marked for removal, the CAS side is kept.
    removable = next(r for r in dup["rows"] if r["txn_id"] == dup["removable_txn_id"])
    assert removable["source"] == "Zerodha"
    assert dup["keep_folio"] == "AMC-FOLIO-1"

    # Nothing deleted by a preview.
    assert len(client.get("/portfolio/metrics").json()) == 2


def test_duplicate_resolve_deletes_the_broker_copy_and_keeps_attribution(
    client: TestClient,
) -> None:
    _seed_cross_source_duplicate(client)

    body = client.post("/portfolio/duplicates/resolve").json()

    assert body["deleted_count"] == 1
    assert body["deleted_amount_paise"] == 4999744  # the broker-side amount
    assert body["source_overrides_written"] == 1

    # Only the CAS position survives...
    holdings = client.get("/portfolio/holdings").json()
    assert [h["folio"] for h in holdings] == ["AMC-FOLIO-1"]

    # ...and it is still attributed to the broker it was really bought through,
    # rather than silently reverting to "Direct".
    surviving = client.get("/portfolio/metrics").json()[0]
    assert surviving["source"] == "Zerodha"
    assert surviving["source_is_override"] is True


def test_duplicate_resolve_is_idempotent(client: TestClient) -> None:
    _seed_cross_source_duplicate(client)
    client.post("/portfolio/duplicates/resolve")

    second = client.post("/portfolio/duplicates/resolve").json()

    assert second["deleted_count"] == 0
    assert client.get("/portfolio/duplicates/preview").json()["removable_count"] == 0


def test_repeats_within_one_folio_are_not_duplicates(client: TestClient) -> None:
    """Two genuine same-day purchases in the same folio must be left alone --
    only cross-folio pairs are cross-source duplicates."""
    csv_content = (
        "folio,amc,scheme_name,txn_type,txn_date,amount,units,isin,amfi_code\n"
        "F1,ICICI,Acme Hybrid,PURCHASE,2021-10-06,1000.00,100.0000,INF001,120700\n"
        "F1,ICICI,Acme Hybrid,PURCHASE,2021-10-06,1000.00,100.0000,INF001,120700\n"
    )
    client.post("/ingest/csv", files={"file": ("t.csv", csv_content, "text/csv")})
    client.post("/portfolio/rebuild-lots")

    assert client.get("/portfolio/duplicates/preview").json()["removable_count"] == 0


def test_two_non_broker_sources_are_reported_but_not_auto_resolved(
    client: TestClient,
) -> None:
    """With no broker side there's no basis to prefer one copy, so nothing is
    marked removable rather than guessing."""
    csv_content = (
        "folio,amc,scheme_name,txn_type,txn_date,amount,units,isin,amfi_code\n"
        "F1,ICICI,Acme Hybrid,PURCHASE,2021-10-06,1000.00,100.0000,INF001,120700\n"
        "F2,ICICI,Acme Hybrid,PURCHASE,2021-10-06,1000.00,100.0000,INF001,120700\n"
    )
    client.post("/ingest/csv", files={"file": ("t.csv", csv_content, "text/csv")})
    client.post("/portfolio/rebuild-lots")

    assert client.get("/portfolio/duplicates/preview").json()["removable_count"] == 0
