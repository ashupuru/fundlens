from fastapi.testclient import TestClient


def _set_risk_profile(client: TestClient) -> None:
    response = client.post(
        "/risk-profile",
        json={"persona": "balanced", "horizon_years": 10},
    )
    assert response.status_code == 200


_RETIREMENT_PROFILE = {
    "current_age": 51,
    "retirement_age": 60,
    "life_expectancy": 85,
    "annual_investment_rupees": "600000",
    "annual_investment_step_up_pct": "10",
    "monthly_withdrawal_today_rupees": "100000",
    "post_retirement_return_pct": "8",
}


def test_profile_endpoints_404_without_a_risk_profile(client: TestClient) -> None:
    assert client.get("/retirement/profile").status_code == 404
    assert client.get("/retirement/projection").status_code == 404


def test_post_profile_requires_an_existing_risk_profile(client: TestClient) -> None:
    response = client.post("/retirement/profile", json=_RETIREMENT_PROFILE)
    assert response.status_code == 422


def test_profile_round_trips(client: TestClient) -> None:
    _set_risk_profile(client)

    response = client.post("/retirement/profile", json=_RETIREMENT_PROFILE)
    assert response.status_code == 200
    body = response.json()
    assert body["current_age"] == 51
    assert body["retirement_age"] == 60
    assert body["life_expectancy"] == 85
    assert body["annual_investment_paise"] == 60_000_000
    assert body["monthly_withdrawal_today_paise"] == 10_000_000

    assert client.get("/retirement/profile").json() == body


def test_projection_reports_reason_when_retirement_fields_unset(client: TestClient) -> None:
    _set_risk_profile(client)

    body = client.get("/retirement/projection").json()

    assert body["reason"] == "retirement_profile_incomplete"
    assert body["years"] == []
    assert body["corpus_at_retirement_paise"] is None


def test_projection_returns_year_by_year_table_once_configured(client: TestClient) -> None:
    _set_risk_profile(client)
    client.post("/retirement/profile", json=_RETIREMENT_PROFILE)

    body = client.get("/retirement/projection").json()

    assert body["reason"] is None
    assert body["current_age"] == 51
    assert body["years_to_retirement"] == 9

    accumulation = [y for y in body["years"] if y["phase"] == "accumulation"]
    retirement = [y for y in body["years"] if y["phase"] == "retirement"]
    # Ages 51..59 accumulate, then retirement runs from 60 -- this fixture's
    # database has no holdings, so the corpus is funded only by the SIP and
    # legitimately depletes before life expectancy (asserted below) rather
    # than running the full 34 years.
    assert [y["age"] for y in accumulation] == list(range(51, 60))
    assert retirement
    assert retirement[0]["age"] == 60

    # Each year's opening balance must be the previous year's closing.
    for previous, current in zip(body["years"], body["years"][1:], strict=False):
        assert current["opening_corpus_paise"] == previous["closing_corpus_paise"]

    assert body["required_corpus_at_retirement_paise"] > 0
    assert body["surplus_or_shortfall_paise"] == (
        body["corpus_at_retirement_paise"] - body["required_corpus_at_retirement_paise"]
    )
    # Today's money vs the nominal figure actually needed at retirement.
    assert body["monthly_withdrawal_today_paise"] == 10_000_000
    assert body["first_year_monthly_withdrawal_paise"] > 10_000_000

    # Underfunded on purpose here: depletion must be reported, never a
    # negative corpus.
    assert body["depletion_age"] is not None
    assert body["years"][-1]["age"] == body["depletion_age"]
    assert all(y["closing_corpus_paise"] >= 0 for y in body["years"])


def test_saving_the_risk_profile_does_not_wipe_retirement_fields(client: TestClient) -> None:
    """The allocation form and the retirement form write to the same
    singleton row -- saving one must not clear the other's inputs."""
    _set_risk_profile(client)
    client.post("/retirement/profile", json=_RETIREMENT_PROFILE)

    client.post("/risk-profile", json={"persona": "aggressive", "horizon_years": 5})

    body = client.get("/retirement/profile").json()
    assert body["current_age"] == 51
    assert body["annual_investment_paise"] == 60_000_000
