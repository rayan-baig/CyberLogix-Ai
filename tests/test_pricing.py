"""Per-unit pricing, the invoice, and the ROI figure."""

import pytest

from pricing import PRICE_BOOK, build_subscription
from store import INDUSTRY_PROFILES, STORE


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
        "cybersecurity": (499.0, "rack"),
        "private_aviation": (349.0, "bay"),
        "superyacht": (399.0, "vessel"),
        "solar_infrastructure": (299.0, "enclosure"),
        "medical_lab": (199.0, "vault"),
        "country_club": (199.0, "kitchen"),
        "logistics": (129.0, "reefer truck"),
        "restaurant": (1000.0, "location"),
    }
    for vertical, (price, unit) in expected.items():
        assert rows[vertical]["monthly_usd"] == price, vertical
        assert rows[vertical]["unit"] == unit, vertical
    assert rows["cybersecurity"]["price_label"] == "$499 / rack / month"


def test_price_list_is_public_and_ordered_by_value(api):
    body = api.get("/api/billing/pricing").json()
    assert body["count"] == 8
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
    assert invoice["monthly_total_usd"] == pytest.approx(3 * 499.0 + 2 * 349.0)
    assert invoice["annual_total_usd"] == pytest.approx((3 * 499.0 + 2 * 349.0) * 12)
    assert invoice["units_total"] == 5

    lines = {l["vertical"]: l for l in invoice["line_items"]}
    assert lines["cybersecurity"]["units"] == 3
    assert lines["cybersecurity"]["line_total_usd"] == pytest.approx(1497.0)
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
    assert invoice["monthly_total_usd"] == 1000.0
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
    assert resp["billing"]["adds_monthly_usd"] == 499.0
    assert resp["billing"]["new_monthly_total_usd"] == 499.0


def test_decommissioning_reports_what_it_removes(
    api, tenant_factory, sensor_factory
):
    headers, _ = tenant_factory()
    sensor_factory(headers, sensor_id="RACK-01", vertical="cybersecurity")
    sensor_factory(headers, sensor_id="FRZ-1", vertical="restaurant")

    resp = api.delete("/api/licenses/me/sensors/RACK-01", headers=headers).json()
    assert resp["billing"]["removes_monthly_usd"] == 499.0
    assert resp["billing"]["new_monthly_total_usd"] == 1000.0


def test_industry_picker_carries_the_price(api):
    rows = {r["vertical"]: r for r in api.get("/api/industries").json()["industries"]}
    assert rows["logistics"]["price_label"] == "$129 / reefer truck / month"
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
    assert answered["subscription_cost_usd"] == 1000.0
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
    assert body["subscription"]["monthly_total_usd"] == 499.0
    assert "roi" in body


def test_billing_is_scoped_to_the_tenant(api, tenant_factory, sensor_factory):
    alice, _ = tenant_factory(company_name="Alice")
    bob, _ = tenant_factory(company_name="Bob")
    sensor_factory(alice, sensor_id="RACK-01", vertical="cybersecurity")

    assert api.get("/api/billing", headers=alice).json()["monthly_total_usd"] == 499.0
    assert api.get("/api/billing", headers=bob).json()["monthly_total_usd"] == 0


def test_billing_requires_authentication(api):
    assert api.get("/api/billing").status_code == 401
    # The price list itself is public — it is a sales page.
    assert api.get("/api/billing/pricing").status_code == 200
