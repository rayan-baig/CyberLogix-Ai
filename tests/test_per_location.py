"""Restaurants are billed per shop, not per thermometer.

The price book called a restaurant unit a "location" and the billing
counted sensors, so one sandwich shop with a walk-in freezer, a walk-in
cooler and a reach-in was invoiced "3 locations" -- $2,997 a month at the
old $999. The price is now $249 per location, and a location is a shop
however many thermometers are inside it.

Every place that turned sensors into money had to agree: the monthly
bill, the part-period catch-up for things added mid-month, the "this
adds $X" message on registering a sensor, and the chain quote. Each is
pinned here. Other sectors -- a rack, a vessel, a tank -- are still
billed per sensor, because each of those is one asset with one sensor.
"""

from datetime import timedelta

import pytest

from contracts import run_billing
from store import STORE, add_months, utc_now

SHOP = 249.0


def _file(api, headers, sensor_id, site_id):
    resp = api.post(f"/api/sites/{site_id}/sensors", headers=headers,
                    json={"sensor_id": sensor_id})
    assert resp.status_code == 200, resp.text


def _site(api, headers, name):
    made = api.post("/api/sites", headers=headers, json={"name": name}).json()
    return (made.get("site") or made)["site_id"]


def _bill(api, headers):
    return api.get("/api/billing", headers=headers).json()


# ---- the monthly bill -------------------------------------------------------


def test_one_shop_with_three_thermometers_is_one_location(
        api, operator_factory, sensor_factory):
    """The case that started this: a single shop that never set up sites."""
    headers, _, _ = operator_factory()
    for name in ("WALKIN-FREEZER", "WALKIN-COOLER", "REACH-IN"):
        sensor_factory(headers, sensor_id=name, vertical="restaurant")

    bill = _bill(api, headers)

    assert bill["monthly_total_usd"] == SHOP
    assert bill["line_items"][0]["units"] == 1
    assert bill["line_items"][0]["description"] == "1 location (3 sensors)"


def test_three_shops_are_three_locations(api, operator_factory, sensor_factory):
    headers, _, _ = operator_factory()
    for shop in range(3):
        site = _site(api, headers, f"Branch {shop}")
        for unit in range(2):
            sid = f"B{shop}-{unit}"
            sensor_factory(headers, sensor_id=sid, vertical="restaurant")
            _file(api, headers, sid, site)

    bill = _bill(api, headers)

    assert bill["monthly_total_usd"] == 3 * SHOP
    assert bill["line_items"][0]["description"] == "3 locations (6 sensors)"


def test_an_unfiled_thermometer_is_named_not_billed_as_a_shop(
        api, operator_factory, sensor_factory):
    """Charging a customer for a location because a sensor was not filed
    under it is the wrong way round to be mistaken. It is said instead, so
    they can see why the count is what it is."""
    headers, _, _ = operator_factory()
    site = _site(api, headers, "Main St")
    sensor_factory(headers, sensor_id="FILED", vertical="restaurant")
    _file(api, headers, "FILED", site)
    sensor_factory(headers, sensor_id="LOOSE", vertical="restaurant")

    bill = _bill(api, headers)

    assert bill["monthly_total_usd"] == SHOP
    assert "1 not assigned to a location" in bill["line_items"][0]["description"]


def test_shops_set_up_but_nothing_filed_is_flagged(
        api, operator_factory, sensor_factory):
    """Two shops exist and neither freezer is filed under one. The count is
    one location -- and the invoice says why, rather than quietly billing
    less than the customer has."""
    headers, _, _ = operator_factory()
    _site(api, headers, "Boca")
    _site(api, headers, "Boynton")
    for n in range(2):
        sensor_factory(headers, sensor_id=f"FRZ-{n}", vertical="restaurant")

    line = _bill(api, headers)["line_items"][0]

    assert line["units"] == 1
    assert "2 not assigned to a location" in line["description"]


def test_other_sectors_are_still_billed_per_sensor(
        api, operator_factory, sensor_factory):
    """A rack is one asset with one sensor. Three racks are three racks."""
    headers, _, _ = operator_factory()
    for n in range(3):
        sensor_factory(headers, sensor_id=f"RACK-{n}", vertical="cybersecurity")

    bill = _bill(api, headers)

    assert bill["monthly_total_usd"] == 3 * 899.0
    assert bill["line_items"][0]["description"] == "3 racks"


# ---- what registering and removing says it costs ---------------------------


def _register(api, headers, sensor_id):
    resp = api.post("/api/licenses/me/sensors", headers=headers, json={
        "sensor_id": sensor_id, "industry_vertical": "restaurant",
        "location_name": "Kitchen"})
    assert resp.status_code == 201, resp.text
    return resp.json()["billing"]


def test_the_first_thermometer_adds_a_shop_and_the_second_adds_nothing(
        api, tenant_factory):
    """Read off the rate card, the second one was announced as another
    $249 a month. It costs nothing: the shop was already on the bill."""
    headers, _ = tenant_factory()

    first = _register(api, headers, "FRZ-1")
    second = _register(api, headers, "FRZ-2")

    assert first["adds_monthly_usd"] == SHOP
    assert second["adds_monthly_usd"] == 0.0
    assert second["new_monthly_total_usd"] == SHOP


def test_removing_one_of_several_thermometers_removes_nothing(
        api, tenant_factory, sensor_factory):
    headers, _ = tenant_factory()
    sensor_factory(headers, sensor_id="FRZ-1", vertical="restaurant")
    sensor_factory(headers, sensor_id="FRZ-2", vertical="restaurant")

    resp = api.delete("/api/licenses/me/sensors/FRZ-1", headers=headers).json()

    assert resp["billing"]["removes_monthly_usd"] == 0.0
    assert resp["billing"]["new_monthly_total_usd"] == SHOP


# ---- the part-period catch-up ----------------------------------------------


def _contracted(api, tenant_factory, owner_headers, sensor_factory, existing):
    headers, tenant = tenant_factory(plan="growth")
    both = {**headers, **owner_headers(headers)}
    for sensor_id in existing:
        sensor_factory(headers, sensor_id=sensor_id, vertical="restaurant")
    assert api.post("/api/contracts", headers=both,
                    json={"term_years": 1}).status_code == 201
    run_billing()
    return both, tenant


def _place_in_time(tenant_id, joined_halfway):
    """Start the contract a month ago; everything existed long before it,
    except the sensors named, which appeared half-way through period 0."""
    sub = STORE.active_subscription(tenant_id)
    sub.started_at = add_months(utc_now(), -1)
    STORE.save_subscription(sub)
    opened, closed = sub.period_start(0), sub.period_start(1)
    halfway = opened + (closed - opened) / 2
    for sensor in STORE.sensors_for(tenant_id):
        sensor.registered_at = (halfway if sensor.sensor_id in joined_halfway
                                else opened - timedelta(days=10))
        STORE._db.put("sensor", sensor.sensor_id, sensor.to_row())


def _arrears(tenant_id):
    invoice = max(STORE.invoices_for(tenant_id), key=lambda i: i.number)
    return [line for line in invoice.lines if line["kind"] == "arrears"]


def test_a_thermometer_added_to_an_existing_shop_owes_no_catch_up(
        api, tenant_factory, owner_headers, sensor_factory):
    """Per sensor, this was half a month of a whole location's price for a
    thermometer bolted into a shop that was already paying."""
    headers, tenant = _contracted(api, tenant_factory, owner_headers,
                                  sensor_factory, ["FRZ-1"])
    _register(api, headers, "FRZ-2")
    _place_in_time(tenant["tenant_id"], joined_halfway={"FRZ-2"})

    run_billing()

    assert _arrears(tenant["tenant_id"]) == []


def test_a_new_shop_mid_period_owes_half_a_location(
        api, tenant_factory, owner_headers, sensor_factory):
    headers, tenant = _contracted(api, tenant_factory, owner_headers,
                                  sensor_factory, ["OLD-1"])
    old_shop = _site(api, headers, "Old shop")
    _file(api, headers, "OLD-1", old_shop)
    new_shop = _site(api, headers, "New shop")
    for sid in ("NEW-1", "NEW-2", "NEW-3"):
        _register(api, headers, sid)
        _file(api, headers, sid, new_shop)
    _place_in_time(tenant["tenant_id"], joined_halfway={"NEW-1", "NEW-2", "NEW-3"})

    run_billing()

    lines = _arrears(tenant["tenant_id"])
    assert len(lines) == 1
    assert lines[0]["quantity"] == 1                  # one shop, not three sensors
    assert lines[0]["amount_usd"] == pytest.approx(SHOP * 0.5, rel=1e-3)
    assert "1 location added mid-period" in lines[0]["description"]


def test_a_per_unit_add_on_is_still_caught_up_per_thermometer(
        api, tenant_factory, owner_headers, sensor_factory, monkeypatch):
    """The subscription is per shop; an add-on priced per covered unit is
    charged per sensor on the monthly invoice, so its catch-up must be too.
    Two thermometers added to an existing shop owe no location and do owe
    the add-on on two sensors for half a month.

    Nothing is priced per unit today, so the add-on is invented, as the
    upsell tests do: the branch is live, and an untested branch in the code
    that decides what to charge is how somebody is billed wrongly at scale.
    """
    import pricing

    monkeypatch.setitem(pricing.ADD_ONS, "per_unit_example", {
        "name": "Per Unit Example", "basis": "per covered unit",
        "monthly_usd": 149.0, "description": ""})
    headers, tenant = _contracted(api, tenant_factory, owner_headers,
                                  sensor_factory, ["FRZ-1"])
    api.post("/api/contracts/add-ons", headers=headers,
             json={"add_ons": ["per_unit_example"]})
    run_billing()
    for sid in ("FRZ-2", "FRZ-3"):
        _register(api, headers, sid)
    _place_in_time(tenant["tenant_id"], joined_halfway={"FRZ-2", "FRZ-3"})

    run_billing()

    lines = _arrears(tenant["tenant_id"])
    assert len(lines) == 1, [line["description"] for line in lines]
    assert "per-unit add-ons on 2 sensors" in lines[0]["description"]
    assert lines[0]["amount_usd"] == pytest.approx(149.0 * 2 * 0.5, rel=1e-3)


# ---- the chain quote ---------------------------------------------------------


def test_the_chain_quote_bills_restaurants_per_branch(api):
    """Multiplying by thermometers per branch quoted a rate card nobody is
    ever charged, and made the volume contract look like a saving."""
    one_each = api.get("/api/v1/enterprise-billing/quote?industry_vertical="
                       "restaurant&total_branch_locations=10&units_per_branch=1").json()
    four_each = api.get("/api/v1/enterprise-billing/quote?industry_vertical="
                        "restaurant&total_branch_locations=10&units_per_branch=4").json()

    assert one_each["per_unit"]["monthly_usd"] == 10 * SHOP
    assert four_each["per_unit"]["monthly_usd"] == 10 * SHOP
    assert four_each["per_unit"]["units"] == 10
