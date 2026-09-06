"""Per-unit pricing, the invoice, and the ROI figure."""

import pytest

from pricing import PRICE_BOOK
from store import INDUSTRY_PROFILES


def test_every_vertical_is_priced():
    """A sensor the invoice cannot account for must be impossible."""
    assert set(PRICE_BOOK) == set(INDUSTRY_PROFILES)
    for vertical, entry in PRICE_BOOK.items():
        assert entry["monthly_usd"] > 0, vertical
        assert entry["unit"], vertical
        assert entry["pitch"], vertical


def test_price_book_matches_the_agreed_rate_card(api):
    rows = {r["vertical"]: r for r in api.get("/api/billing/pricing").json()["pricing"]}
    expected = {
        "superyacht": (4999.0, "vessel"),
        "private_aviation": (1999.0, "bay"),
        "medical_lab": (1499.0, "vault"),
        "country_club": (1499.0, "kitchen"),
        "restaurant": (999.0, "location"),
        "cybersecurity": (899.0, "rack"),
        "solar_infrastructure": (899.0, "enclosure"),
        "logistics": (749.0, "reefer truck"),
    }
    for vertical, (price, unit) in expected.items():
        assert rows[vertical]["monthly_usd"] == price, vertical
        assert rows[vertical]["unit"] == unit, vertical
    assert rows["cybersecurity"]["price_label"] == "$899 / rack / month"


def test_price_list_is_public_and_ordered_by_value(api):
    from store import INDUSTRY_PROFILES

    body = api.get("/api/billing/pricing").json()
    assert body["count"] == len(INDUSTRY_PROFILES)
    prices = [r["monthly_usd"] for r in body["pricing"]]
    assert prices == sorted(prices, reverse=True)


def test_a_mixed_estate_is_billed_per_unit(api, tenant_factory, sensor_factory):
    """Three racks and two bays is 3x499 + 2x349."""
    headers, _ = tenant_factory(plan="enterprise")
    for index in range(3):
        sensor_factory(headers, sensor_id=f"RACK-{index}", vertical="cybersecurity")
    for index in range(2):
        sensor_factory(headers, sensor_id=f"BAY-{index}", vertical="private_aviation")

    invoice = api.get("/api/billing", headers=headers).json()
    assert invoice["monthly_total_usd"] == pytest.approx(3 * 899.0 + 2 * 1999.0)
    assert invoice["annual_total_usd"] == pytest.approx(
        (3 * 899.0 + 2 * 1999.0) * 12
    )
    assert invoice["units_total"] == 5

    lines = {l["vertical"]: l for l in invoice["line_items"]}
    assert lines["cybersecurity"]["units"] == 3
    assert lines["cybersecurity"]["line_total_usd"] == pytest.approx(2697.0)
    assert lines["cybersecurity"]["description"] == "3 racks"
    assert lines["private_aviation"]["description"] == "2 bays"


def test_unit_names_are_pluralised(api, tenant_factory, sensor_factory):
    headers, _ = tenant_factory()
    sensor_factory(headers, sensor_id="V-1", vertical="superyacht")
    one = api.get("/api/billing", headers=headers).json()
    assert one["line_items"][0]["description"] == "1 vessel"

    sensor_factory(headers, sensor_id="V-2", vertical="superyacht")
    two = api.get("/api/billing", headers=headers).json()
    assert two["line_items"][0]["description"] == "2 vessels"


def test_empty_estate_costs_nothing(api, tenant_factory):
    headers, _ = tenant_factory()
    invoice = api.get("/api/billing", headers=headers).json()
    assert invoice["monthly_total_usd"] == 0
    assert invoice["line_items"] == []


def test_trial_is_priced_but_not_charged(api, tenant_factory, sensor_factory):
    headers, _ = tenant_factory(plan="trial")
    sensor_factory(headers, sensor_id="FRZ-1", vertical="restaurant")

    invoice = api.get("/api/billing", headers=headers).json()
    assert invoice["billable"] is False
    assert invoice["monthly_total_usd"] == 999.0
    assert invoice["effective_monthly_usd"] == 0.0
    assert "not charged" in invoice["note"]


def test_registering_a_sensor_reports_what_it_adds(api, tenant_factory):
    headers, _ = tenant_factory()
    resp = api.post(
        "/api/licenses/me/sensors",
        headers=headers,
        json={
            "sensor_id": "RACK-01",
            "industry_vertical": "cybersecurity",
            "location_name": "Hall B",
        },
    ).json()
    assert resp["billing"]["adds_monthly_usd"] == 899.0
    assert resp["billing"]["new_monthly_total_usd"] == 899.0


def test_decommissioning_reports_what_it_removes(
    api, tenant_factory, sensor_factory
):
    headers, _ = tenant_factory()
    sensor_factory(headers, sensor_id="RACK-01", vertical="cybersecurity")
    sensor_factory(headers, sensor_id="FRZ-1", vertical="restaurant")

    resp = api.delete("/api/licenses/me/sensors/RACK-01", headers=headers).json()
    assert resp["billing"]["removes_monthly_usd"] == 899.0
    assert resp["billing"]["new_monthly_total_usd"] == 999.0


def test_industry_picker_carries_the_price(api):
    rows = {r["vertical"]: r for r in api.get("/api/industries").json()["industries"]}
    assert rows["logistics"]["price_label"] == "$749 / reefer truck / month"
    assert "cargo rejection" in rows["logistics"]["pitch"]


def test_roi_counts_only_answered_incidents(
    api, tenant_factory, sensor_factory
):
    """An unanswered alert is a warning nobody heeded, not a save."""
    headers, _ = tenant_factory()
    sensor_factory(headers, sensor_id="FRZ-1", vertical="restaurant")
    first = api.post(
        "/api/sensor-pulse", headers=headers,
        json={"sensor_id": "FRZ-1", "temperature_fahrenheit": 45.0},
    ).json()["incident_id"]

    unanswered = api.get("/api/billing/roi", headers=headers).json()
    assert unanswered["incidents_total"] == 1
    assert unanswered["quantified_saves"] == 0
    assert unanswered["loss_avoided_usd"] == 0

    api.post(f"/api/voice/acknowledge/{first}", headers=headers, json={})
    answered = api.get("/api/billing/roi", headers=headers).json()
    assert answered["quantified_saves"] == 1
    assert answered["loss_avoided_usd"] == 15000.0
    assert answered["subscription_cost_usd"] == 999.0
    assert answered["return_multiple"] == pytest.approx(15.0, abs=0.1)


def test_roi_never_invents_a_figure(api, tenant_factory, sensor_factory):
    """Verticals with no supplied loss figure are listed, not estimated."""
    headers, _ = tenant_factory()
    sensor_factory(headers, sensor_id="VAULT-1", vertical="medical_lab")
    incident = api.post(
        "/api/sensor-pulse", headers=headers,
        json={"sensor_id": "VAULT-1", "temperature_fahrenheit": 55.0},
    ).json()["incident_id"]
    api.post(f"/api/voice/acknowledge/{incident}", headers=headers, json={})

    body = api.get("/api/billing/roi", headers=headers).json()
    assert body["incidents_answered"] == 1
    assert body["quantified_saves"] == 0
    assert body["unquantified_saves"] == 1
    assert body["loss_avoided_usd"] == 0
    assert body["detail"][0]["loss_avoided_usd"] is None


def test_console_overview_carries_the_commercials(
    api, tenant_factory, sensor_factory
):
    headers, _ = tenant_factory()
    sensor_factory(headers, sensor_id="RACK-01", vertical="cybersecurity")
    body = api.get("/api/console/overview", headers=headers).json()
    assert body["subscription"]["monthly_total_usd"] == 899.0
    assert "roi" in body


def test_billing_is_scoped_to_the_tenant(api, tenant_factory, sensor_factory):
    alice, _ = tenant_factory(company_name="Alice")
    bob, _ = tenant_factory(company_name="Bob")
    sensor_factory(alice, sensor_id="RACK-01", vertical="cybersecurity")

    assert api.get("/api/billing", headers=alice).json()["monthly_total_usd"] == 899.0
    assert api.get("/api/billing", headers=bob).json()["monthly_total_usd"] == 0


def test_billing_requires_authentication(api):
    assert api.get("/api/billing").status_code == 401
    # The price list itself is public — it is a sales page.
    assert api.get("/api/billing/pricing").status_code == 200


# ---- contract terms ------------------------------------------------------


def test_the_escalator_compounds_across_the_term():
    """Five percent a year is sixteen percent more contract over three."""
    from pricing import escalated_schedule

    schedule = escalated_schedule(10000.0, 3)
    assert [row["monthly_usd"] for row in schedule] == [10000.0, 10500.0, 11025.0]
    total = sum(row["annual_usd"] for row in schedule)
    assert total == 378300.0
    # 5.1% more contract value than three flat years, for no extra delivery.
    flat = 10000.0 * 36
    assert round((total - flat) / flat * 100, 1) == 5.1


def test_a_single_year_carries_no_escalator():
    from pricing import escalated_schedule

    schedule = escalated_schedule(5000.0, 1)
    assert len(schedule) == 1
    assert schedule[0]["monthly_usd"] == 5000.0


def test_the_term_is_capped():
    """A ten-year escalator is a number nobody signs."""
    from pricing import MAX_CONTRACT_YEARS, escalated_schedule

    assert len(escalated_schedule(1000.0, 99)) == MAX_CONTRACT_YEARS


def test_every_add_on_is_a_fixed_fee():
    """The one rule: a quiet year must still pay for a standing obligation."""
    from pricing import ADD_ONS, add_on_price

    for key, entry in ADD_ONS.items():
        assert entry["monthly_usd"] > 0, key
        assert entry["basis"] in {"per estate", "per covered unit"}, key
        # Nothing scales with a saving, only with the estate.
        assert "percent" not in entry["basis"]
        assert add_on_price(key, 0) >= 0


def test_a_per_unit_add_on_scales_with_the_estate():
    from pricing import add_on_price

    assert add_on_price("assurance", 10) == 1490.0
    assert add_on_price("vault", 10) == 499.0  # per estate, flat


def test_the_deal_ties_setup_term_and_add_ons_together(
    api, operator_factory, sensor_factory
):
    headers, _, _ = operator_factory(plan="enterprise")
    api.post("/api/sites", headers=headers, json={"name": "Boca"})
    api.post("/api/sites", headers=headers, json={"name": "Boynton"})
    for n in range(2):
        sensor_factory(headers, sensor_id=f"FRZ-{n}", vertical="restaurant")

    deal = api.get("/api/billing/deal?years=3&annual_prepay=true"
                   "&include_add_ons=assurance,vault", headers=headers).json()

    assert deal["subscription_monthly_usd"] == 2 * 999.0
    assert deal["add_ons_monthly_usd"] == 2 * 149.0 + 499.0
    assert deal["setup"]["sites_billed"] == 2
    assert deal["setup"]["one_time_usd"] == 3000.0
    assert deal["term"]["years"] == 3
    assert deal["term"]["escalator_percent"] == 5.0
    assert len(deal["term"]["schedule"]) == 3
    assert deal["annual_prepay"]["discount_percent"] == 10.0
    assert deal["annual_prepay"]["discount_usd"] > 0
    # Setup is never discounted by the prepay.
    year_one = deal["term"]["schedule"][0]["annual_usd"]
    assert deal["total_first_year_usd"] == round(
        year_one * 0.9 + 3000.0, 2
    )


def test_an_estate_with_no_recorded_sites_still_pays_one_setup(
    api, operator_factory, sensor_factory
):
    """Every estate is commissioned somewhere."""
    headers, _, _ = operator_factory(plan="enterprise")
    sensor_factory(headers, sensor_id="FRZ-1", vertical="restaurant")

    deal = api.get("/api/billing/deal", headers=headers).json()
    assert deal["sites"] == 0
    assert deal["setup"]["sites_billed"] == 1
    assert deal["setup"]["one_time_usd"] == 1500.0


def test_an_unknown_add_on_is_refused(api, operator_factory):
    headers, _, _ = operator_factory()
    resp = api.get("/api/billing/deal?include_add_ons=free_ponies",
                   headers=headers)
    assert resp.status_code == 400


def test_the_new_verticals_are_priced_for_their_market():
    """Wine and art is the highest per-asset price in the book."""
    from pricing import PRICE_BOOK

    assert PRICE_BOOK["wine_and_art"]["monthly_usd"] == 2499.0
    assert PRICE_BOOK["pharmacy"]["monthly_usd"] == 1299.0
    assert PRICE_BOOK["cannabis"]["monthly_usd"] == 1199.0
