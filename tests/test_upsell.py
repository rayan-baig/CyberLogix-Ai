"""Two places the product knew about money and did not ask for it.

The pipeline could say "this estate is not buying Loss Assurance, and
that is $8,940 a year" from the day it shipped, with nothing anywhere
that would let anybody buy it. And the seat limit — the highest-intent
moment the product ever gets, somebody standing in a walk-in with a
sensor in their hand — answered "Upgrade to add more", naming no tier, no
price and no route.
"""

import pytest

from contracts import run_billing
from store import STORE, add_months, utc_now


@pytest.fixture()
def contracted(api, tenant_factory, owner_headers, sensor_factory):
    headers, tenant = tenant_factory(plan="growth")
    owner = owner_headers(headers)
    both = {**headers, **owner}
    for i in range(4):
        sensor_factory(headers, f"FRZ-{i}", "restaurant")
    assert api.post("/api/contracts", headers=both,
                    json={"term_years": 3}).status_code == 201
    run_billing()
    return both, tenant


# ---- the seat limit is a sale, not an error ----------------------------


def test_the_seat_limit_names_the_plan_the_price_and_the_route(
    api, tenant_factory, sensor_factory, owner_headers
):
    headers, _ = tenant_factory(plan="trial")
    for i in range(5):
        sensor_factory(headers, f"FRZ-{i}", "restaurant")

    refused = api.post("/api/licenses/me/sensors", headers=headers, json={
        "sensor_id": "FRZ-5", "industry_vertical": "restaurant",
        "location_name": "Sixth"})
    assert refused.status_code == 409
    detail = refused.json()["detail"]

    assert "Growth" in detail, "the refusal does not say which plan to move to"
    assert "50" in detail, "it does not say how much room that buys"
    assert "$999" in detail, "it does not say what a unit costs"
    assert "/api/licenses/me/plan" in detail, "it does not say how"
    assert "nothing is lost" in detail


def test_the_sale_the_message_offers_actually_closes(
    api, tenant_factory, sensor_factory, owner_headers
):
    headers, _ = tenant_factory(plan="trial")
    owner = owner_headers(headers)
    for i in range(5):
        sensor_factory(headers, f"FRZ-{i}", "restaurant")

    assert api.post("/api/licenses/me/plan", headers={**headers, **owner},
                    json={"plan": "growth"}).status_code == 200
    assert api.post("/api/licenses/me/sensors", headers=headers, json={
        "sensor_id": "FRZ-5", "industry_vertical": "restaurant",
        "location_name": "Sixth"}).status_code == 201


def test_the_largest_plan_says_so_instead_of_offering_a_bigger_one(
    api, tenant_factory, sensor_factory, monkeypatch
):
    """No tier above Enterprise, so pointing at one would be a lie."""
    import licenses

    monkeypatch.setitem(licenses.PLAN_TIERS["enterprise"], "max_sensors", 1)
    headers, _ = tenant_factory(plan="enterprise")
    sensor_factory(headers, "FRZ-0", "restaurant")

    refused = api.post("/api/licenses/me/sensors", headers=headers, json={
        "sensor_id": "FRZ-1", "industry_vertical": "restaurant",
        "location_name": "Second"})
    assert refused.status_code == 409
    assert "largest one" in refused.json()["detail"]
    assert "/api/licenses/me/plan" not in refused.json()["detail"]


# ---- add-ons can be bought ---------------------------------------------


def test_an_add_on_can_be_bought_without_a_conversation(api, contracted):
    headers, tenant = contracted
    before = api.get("/api/contracts/pipeline", headers=headers).json()
    assert before["identified_annual_usd"] > 0
    assert "assurance" in {o["key"] for o in before["opportunities"]}

    bought = api.post("/api/contracts/add-ons", headers=headers,
                      json={"add_ons": ["assurance", "vault"]})
    assert bought.status_code == 200, bought.text
    assert bought.json()["added"] == ["assurance", "vault"]

    after = api.get("/api/contracts/pipeline", headers=headers).json()
    assert "assurance" not in {o["key"] for o in after["opportunities"]}
    assert after["identified_annual_usd"] < before["identified_annual_usd"]


def test_what_was_bought_reaches_the_next_invoice(api, contracted):
    headers, tenant = contracted
    api.post("/api/contracts/add-ons", headers=headers,
             json={"add_ons": ["assurance", "vault"]})

    sub = STORE.active_subscription(tenant["tenant_id"])
    sub.started_at = add_months(utc_now(), -1)
    STORE.save_subscription(sub)
    run_billing()

    invoice = max(STORE.invoices_for(tenant["tenant_id"]), key=lambda i: i.number)
    add_ons = [l for l in invoice.lines if l["kind"] == "add_on"]
    assert len(add_ons) == 2
    # Per covered unit for one, per estate for the other.
    assert sum(l["amount_usd"] for l in add_ons) == pytest.approx(
        149.0 * 4 + 499.0
    )


def test_buying_is_never_backdated(api, contracted):
    """Charging for a month of something that was not switched on is the
    kind of line that starts a dispute."""
    headers, tenant = contracted
    already = STORE.invoices_for(tenant["tenant_id"])[0]
    api.post("/api/contracts/add-ons", headers=headers,
             json={"add_ons": ["assurance"]})

    unchanged = STORE.get_invoice(already.invoice_id)
    assert unchanged.total_usd == already.total_usd
    assert not [l for l in unchanged.lines if l["kind"] == "add_on"]


def test_an_add_on_can_be_dropped_again(api, contracted):
    headers, _ = contracted
    api.post("/api/contracts/add-ons", headers=headers,
             json={"add_ons": ["assurance", "vault"]})
    out = api.post("/api/contracts/add-ons", headers=headers,
                   json={"add_ons": ["vault"]}).json()
    assert out["dropped"] == ["assurance"]
    assert out["contract"]["add_ons"] == ["vault"]


def test_the_set_is_replaced_not_merged(api, contracted):
    """Two people clicking at once must not build a contract neither chose."""
    headers, _ = contracted
    api.post("/api/contracts/add-ons", headers=headers,
             json={"add_ons": ["assurance"]})
    out = api.post("/api/contracts/add-ons", headers=headers,
                   json={"add_ons": ["benchmarks"]}).json()
    assert out["contract"]["add_ons"] == ["benchmarks"]


def test_an_unknown_add_on_is_refused(api, contracted):
    headers, _ = contracted
    resp = api.post("/api/contracts/add-ons", headers=headers,
                    json={"add_ons": ["gold_plating"]})
    assert resp.status_code == 400
    assert "gold_plating" in resp.json()["detail"]


def test_buying_needs_a_contract_to_attach_to(
    api, tenant_factory, owner_headers, sensor_factory
):
    headers, _ = tenant_factory(plan="growth")
    owner = owner_headers(headers)
    sensor_factory(headers, "FRZ-1", "restaurant")
    resp = api.post("/api/contracts/add-ons", headers={**headers, **owner},
                    json={"add_ons": ["assurance"]})
    assert resp.status_code == 404
    assert "POST /api/contracts" in resp.json()["detail"]


def test_buying_is_an_owners_decision(api, tenant_factory, sensor_factory,
                                      owner_headers, contracted):
    headers, _ = contracted
    api.post("/api/accounts/users", headers=headers,
             json={"email": "ops@example.com", "full_name": "Sam",
                   "role": "operator", "password": "correct-horse-battery"})
    token = api.post("/api/accounts/login", json={
        "email": "ops@example.com", "password": "correct-horse-battery"
    }).json()["token"]
    resp = api.post("/api/contracts/add-ons",
                    headers={"Authorization": f"Bearer {token}"},
                    json={"add_ons": ["assurance"]})
    assert resp.status_code == 403


def test_the_purchase_is_in_the_audit_trail(api, contracted):
    headers, _ = contracted
    api.post("/api/contracts/add-ons", headers=headers,
             json={"add_ons": ["assurance"]})
    audit = api.get("/api/accounts/audit", headers=headers).json()["entries"]
    bought = [e for e in audit if e["action"] == "contract.add_ons"]
    assert len(bought) == 1
    assert "+Loss Assurance" in bought[0]["detail"]


def test_the_console_offers_a_button_for_every_priced_add_on():
    """The list said what was not being sold and could not sell it."""
    from pathlib import Path

    page = Path("static/console.html").read_text()
    assert "data-buy=" in page
    assert "/api/contracts/add-ons" in page
    # And it sends the whole set, not the one that was clicked.
    assert "new Set(contract.add_ons" in page


def test_a_per_unit_add_on_owes_arrears_on_units_added_mid_period(
    api, contracted
):
    """The cover ran on those units. Something has to charge for it.

    Measured before the fix: six units added halfway through a month on an
    estate carrying Loss Assurance produced $2,997 of arrears where $3,444
    was owed. The subscription was caught up and the per-unit add-on was
    not, so the guarantee was in force for half a month on six units for
    nothing.
    """
    headers, tenant = contracted
    api.post("/api/contracts/add-ons", headers=headers,
             json={"add_ons": ["assurance"]})
    run_billing()

    for i in range(6):
        api.post("/api/licenses/me/sensors", headers=headers, json={
            "sensor_id": f"NEW-{i}", "industry_vertical": "restaurant",
            "location_name": "Annexe"})

    sub = STORE.active_subscription(tenant["tenant_id"])
    sub.started_at = add_months(utc_now(), -1)
    STORE.save_subscription(sub)
    half = sub.period_start(0) + (sub.period_start(1) - sub.period_start(0)) / 2
    for sensor in STORE.sensors_for(tenant["tenant_id"]):
        if sensor.sensor_id.startswith("NEW-"):
            sensor.registered_at = half
            STORE._db.put("sensor", sensor.sensor_id, sensor.to_row())
    run_billing()

    invoice = max(STORE.invoices_for(tenant["tenant_id"]), key=lambda i: i.number)
    arrears = [l for l in invoice.lines if l["kind"] == "arrears"][0]
    # Six units, half a month, at the unit rate *plus* the per-unit add-on.
    assert arrears["amount_usd"] == pytest.approx(
        (999.0 + 149.0) * 6 * 0.5, rel=1e-3
    )
    assert "add-ons" in arrears["description"]


def test_a_per_estate_add_on_owes_no_arrears(api, contracted):
    """It is charged in full for the period whatever the unit count does."""
    headers, tenant = contracted
    api.post("/api/contracts/add-ons", headers=headers,
             json={"add_ons": ["vault"]})
    run_billing()

    api.post("/api/licenses/me/sensors", headers=headers, json={
        "sensor_id": "NEW-1", "industry_vertical": "restaurant",
        "location_name": "Annexe"})
    sub = STORE.active_subscription(tenant["tenant_id"])
    sub.started_at = add_months(utc_now(), -1)
    STORE.save_subscription(sub)
    half = sub.period_start(0) + (sub.period_start(1) - sub.period_start(0)) / 2
    for sensor in STORE.sensors_for(tenant["tenant_id"]):
        if sensor.sensor_id == "NEW-1":
            sensor.registered_at = half
            STORE._db.put("sensor", sensor.sensor_id, sensor.to_row())
    run_billing()

    invoice = max(STORE.invoices_for(tenant["tenant_id"]), key=lambda i: i.number)
    arrears = [l for l in invoice.lines if l["kind"] == "arrears"][0]
    assert arrears["amount_usd"] == pytest.approx(999.0 * 0.5, rel=1e-3)
    assert "add-ons" not in arrears["description"]
