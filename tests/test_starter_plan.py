"""Starter: one location, up to three fridges or freezers, one flat price.

What it costs comes from pricing.py, what it holds from domain.PLAN_TIERS,
and every page that names it reads both. The fourth unit and the second
location ask for Growth in words and charge nothing; the invoice bills the
flat price, and a move to Growth part-way through a period bills only the
difference for the days after the move.
"""

import json
import re
import shutil
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

import pytest

import pricing
from contracts import run_billing
from domain import PLAN_TIERS
from store import STORE, add_months, iso, utc_now

ROOT = Path(__file__).resolve().parent.parent
SHOP = pricing.PRICE_BOOK["restaurant"]["monthly_usd"]
STARTER = pricing.STARTER_MONTHLY_USD


def _starter(api, tenant_factory, owner_headers, units=1, company="Corner Cafe"):
    headers, tenant = tenant_factory(plan="starter", company_name=company)
    both = {**headers, **owner_headers(headers)}
    for n in range(units):
        made = api.post("/api/licenses/me/sensors", headers=headers, json={
            "sensor_id": f"CAFE-{n + 1}", "industry_vertical": "restaurant",
            "location_name": ["Display fridge", "Under-counter", "Freezer"][n % 3]})
        assert made.status_code == 201, made.text
    return both, tenant


# ---- the plan, from the code ----------------------------------------------


def test_the_plan_is_in_the_plan_structure_with_the_owners_price():
    tier = PLAN_TIERS["starter"]
    assert tier["max_sensors"] == 3 and tier["max_locations"] == 1
    assert tier["voice_escalation"] is False
    assert pricing.STARTER_MONTHLY_USD == 79.0
    terms = pricing.starter_terms()
    assert terms["monthly_label"] == "$79" and terms["max_units"] == 3
    # Safety is not paywalled: texts and the site-wide alert are on it.
    assert any("site-wide" in item for item in terms["includes"])
    assert any("Text alerts" in item for item in terms["includes"])


def test_the_site_wide_alert_runs_on_starter(api, tenant_factory, owner_headers):
    """Nothing in the site-wide alert asks for a plan: Starter has it."""
    import sitewide

    source = Path(sitewide.__file__).read_text()
    assert "entitlements" not in source and "voice_escalation\"]" not in source


def test_the_public_plan_list_and_the_price_list_carry_it(api):
    plans = {p["plan"]: p for p in api.get("/api/licenses/plans").json()["plans"]}
    assert plans["starter"]["monthly_usd"] == STARTER
    assert plans["growth"]["monthly_usd"] is None
    listed = api.get("/api/billing/pricing").json()
    starter = next(p for p in listed["plans"] if p["plan"] == "starter")
    assert starter["monthly_usd"] == STARTER and starter["max_locations"] == 1
    assert listed["starter"]["monthly_usd"] == STARTER


def test_a_changed_price_reaches_every_page(api, monkeypatch):
    monkeypatch.setattr(pricing, "STARTER_MONTHLY_USD", 85.0)
    assert "Starter: $85 a month" in api.get("/pricing").text
    assert api.get("/api/signup/sectors").json()["starter"]["monthly_label"] == "$85"
    import helpdesk

    assert "$85 a month" in " ".join(" ".join(i["a"]) for i in helpdesk.faq())
    assert "$85 a month for one" in api.get("/for/restaurant/sheet").text


def test_the_pricing_page_shows_starter_from_the_code(api):
    page = api.get("/pricing").text
    assert "%%" not in page
    assert f'data-starter-rate="{STARTER!r}"' in page
    assert "Starter: $79 a month" in page
    assert "<td>Starter</td><td class=\"num\">3</td>" in page
    assert "$79, one location" in page


def test_signup_the_help_page_and_the_sales_sheet_show_starter(api):
    sectors = api.get("/api/signup/sectors").json()
    assert sectors["starter"]["monthly_usd"] == STARTER
    assert "info.starter.monthly_label" in (ROOT / "static/signup.js").read_text()
    import helpdesk

    price = " ".join(next(i for i in helpdesk.faq() if i["id"] == "price")["a"])
    plans = " ".join(next(i for i in helpdesk.faq() if i["id"] == "plans")["a"])
    assert "$79 a month for one location and up to 3 fridges" in price
    assert "Starter: up to 3 sensors" in plans
    assert "nothing is charged until you do" in plans
    sheet = re.sub(r"\s+", " ", api.get("/for/restaurant/sheet").text)
    assert "Starter: $79 a month for one location and up to 3 fridges" in sheet
    # Not offered on the sheet for a trade it is not for.
    assert "Starter: $79" not in api.get("/for/cybersecurity/sheet").text


# ---- the calculator agrees with pricing.py --------------------------------


def _quote_in_node(cases):
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    script = ROOT / "static" / "pricing.js"
    harness = (f"const calc = require({json.dumps(str(script))});\n"
               f"console.log(JSON.stringify({json.dumps(cases)}.map((c) => calc.quote(c))));\n")
    done = subprocess.run([node, "-e", harness], capture_output=True, text=True, timeout=20)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def _boxes(page):
    return {m.group(1): (float(m.group(2)), m.group(3)) for m in re.finditer(
        r'class="calc-addon" value="([a-z_]+)" data-rate="([0-9.]+)" '
        r'data-name="[^"]*" data-basis="([^"]+)"', page)}


def test_the_calculator_prices_starter_and_the_bundle_as_pricing_py(api):
    page = api.get("/pricing").text
    starter_rate = float(re.search(r'data-starter-rate="([0-9.]+)"', page).group(1))
    rates = {m.group(1): float(m.group(2)) for m in re.finditer(
        r'<option value="([a-z_]+)" data-rate="([0-9.]+)"', page)}
    boxes = _boxes(page)
    bundle = pricing.BUNDLE_ADD_ON
    estates = [
        ("starter", {"restaurant": 1}, [], None),
        ("starter", {"restaurant": 3}, [bundle], None),
        (None, {"restaurant": 4}, [bundle], 4),
        (None, {"restaurant": 2, "pharmacy": 1}, [bundle, "vault"], 3),
        (None, {"cybersecurity": 5}, ["benchmarks"], 1),
    ]
    cases = [{
        "starter": starter_rate if plan == "starter" else None,
        "lines": [{"rate": rates[v], "count": n} for v, n in units.items()],
        "addOns": [{"rate": boxes[k][0], "basis": boxes[k][1]} for k in add_ons],
        "locations": locations,
    } for plan, units, add_ons, locations in estates]
    got = _quote_in_node(cases)
    for (plan, units, add_ons, locations), monthly in zip(estates, got):
        assert monthly == pytest.approx(
            pricing.quote_monthly(units, add_ons, plan=plan, locations=locations), abs=0.001)
    assert got[0] == STARTER
    assert got[1] == STARTER + pricing.ADD_ONS[bundle]["monthly_usd"]


def test_starter_quotes_refuse_the_estate_extras():
    with pytest.raises(ValueError):
        pricing.quote_monthly({"restaurant": 1}, ["vault"], plan="starter")


def test_the_script_holds_neither_price():
    source = (ROOT / "static" / "pricing.js").read_text()
    code = re.sub(r"/\*.*?\*/|//[^\n]*", "", source, flags=re.S)
    numbers = {float(n) for n in re.findall(r"(?<![\w.])\d+(?:\.\d+)?", code)}
    assert STARTER not in numbers
    assert pricing.ADD_ONS[pricing.BUNDLE_ADD_ON]["monthly_usd"] not in numbers
    assert "dataset.starterRate" in code


# ---- the limits, and the way up --------------------------------------------


def test_a_fourth_unit_asks_for_growth_and_charges_nothing(api, tenant_factory, owner_headers):
    both, tenant = _starter(api, tenant_factory, owner_headers, units=3)
    before = api.get("/api/billing", headers=both).json()["monthly_total_usd"]
    refused = api.post("/api/licenses/me/sensors", headers=both, json={
        "sensor_id": "CAFE-4", "industry_vertical": "restaurant",
        "location_name": "Back freezer"})
    assert refused.status_code == 409
    detail = refused.json()["detail"]
    assert "unit 4" in detail and "Move to Growth" in detail
    assert "Nothing has been charged" in detail
    assert len(STORE.sensors_for(tenant["tenant_id"])) == 3
    assert api.get("/api/billing", headers=both).json()["monthly_total_usd"] == before == STARTER


def test_a_second_location_asks_for_growth(api, tenant_factory, owner_headers):
    both, tenant = _starter(api, tenant_factory, owner_headers)
    first = api.post("/api/sites", headers=both, json={"name": "High Street"})
    assert first.status_code == 201, first.text
    second = api.post("/api/sites", headers=both, json={"name": "Market Square"})
    assert second.status_code == 409
    assert "location 2" in second.json()["detail"]
    assert "Move to Growth" in second.json()["detail"]
    assert len(STORE.sites_for(tenant["tenant_id"])) == 1


def test_a_trade_starter_is_not_for_asks_for_growth(api, tenant_factory, owner_headers):
    both, _ = _starter(api, tenant_factory, owner_headers)
    refused = api.post("/api/licenses/me/sensors", headers=both, json={
        "sensor_id": "VAX-1", "industry_vertical": "pharmacy",
        "location_name": "Vaccine fridge"})
    assert refused.status_code == 409
    assert "Move to Growth" in refused.json()["detail"]


def test_moving_up_to_growth_lifts_the_limits(api, tenant_factory, owner_headers):
    both, tenant = _starter(api, tenant_factory, owner_headers, units=3)
    moved = api.post("/api/licenses/me/plan", headers=both, json={"plan": "growth"})
    assert moved.status_code == 200, moved.text
    assert api.post("/api/licenses/me/sensors", headers=both, json={
        "sensor_id": "CAFE-4", "industry_vertical": "restaurant",
        "location_name": "Back freezer"}).status_code == 201
    change = STORE.plan_changes(tenant["tenant_id"])[-1]
    assert (change["from_plan"], change["to_plan"]) == ("starter", "growth")


def test_a_trial_can_choose_starter_but_not_with_more_than_it_holds(
        api, tenant_factory, owner_headers, sensor_factory):
    headers, _ = tenant_factory(plan="trial")
    both = {**headers, **owner_headers(headers)}
    for n in range(4):
        sensor_factory(headers, sensor_id=f"T-{n}", vertical="restaurant")
    refused = api.post("/api/licenses/me/plan", headers=both, json={"plan": "starter"})
    assert refused.status_code == 409
    assert "4 units are registered" in refused.json()["detail"]
    api.delete("/api/licenses/me/sensors/T-3", headers=headers)
    assert api.post("/api/licenses/me/plan", headers=both,
                    json={"plan": "starter"}).status_code == 200


def test_growth_cannot_drop_itself_to_starter(api, tenant_factory, owner_headers):
    headers, _ = tenant_factory(plan="growth")
    both = {**headers, **owner_headers(headers)}
    refused = api.post("/api/licenses/me/plan", headers=both, json={"plan": "starter"})
    assert refused.status_code == 409


def test_a_trial_at_its_limit_is_pointed_past_starter(api, tenant_factory, sensor_factory):
    """Starter holds fewer units than the trial, so it is not the way past
    the trial's limit: Growth is."""
    import licenses

    assert licenses.next_tier_above("trial") == "growth"
    assert licenses.next_tier_above("starter") == "growth"


def test_starter_takes_only_the_bundle(api, tenant_factory, owner_headers):
    both, _ = _starter(api, tenant_factory, owner_headers)
    refused = api.post("/api/contracts", headers=both,
                       json={"term_years": 1, "add_ons": ["vault"]})
    assert refused.status_code == 409
    assert "not on Starter" in refused.json()["detail"]
    ok = api.post("/api/contracts", headers=both,
                  json={"term_years": 1, "add_ons": [pricing.BUNDLE_ADD_ON]})
    assert ok.status_code == 201, ok.text


# ---- the invoice ------------------------------------------------------------


def test_the_invoice_bills_the_flat_price(api, tenant_factory, owner_headers):
    both, tenant = _starter(api, tenant_factory, owner_headers, units=3)
    priced = api.get("/api/billing", headers=both).json()
    assert priced["billing_model"] == "starter"
    [line] = priced["line_items"]
    assert line["line_total_usd"] == STARTER
    assert line["description"] == "Starter plan: 1 location, 3 of 3 units"
    assert api.post("/api/contracts", headers=both, json={"term_years": 1}).status_code == 201
    run_billing()
    [invoice] = STORE.invoices_for(tenant["tenant_id"])
    assert invoice.total_usd == STARTER
    assert "Starter plan" in invoice.lines[0]["description"]


def _backdate_period_zero(tenant_id, moved_fraction=0.5):
    sub = STORE.active_subscription(tenant_id)
    sub.started_at = add_months(utc_now(), -1)
    STORE.save_subscription(sub)
    opened, closed = sub.period_start(0), sub.period_start(1)
    for sensor in STORE.sensors_for(tenant_id):
        sensor.registered_at = opened - timedelta(days=10)
        STORE._db.put("sensor", sensor.sensor_id, sensor.to_row())
    moved = opened + (closed - opened) * moved_fraction
    for change in STORE.plan_changes(tenant_id):
        change["at"] = iso(moved)
        STORE._db.put("plan_change", change["change_id"], change)
    return opened, closed, moved


def test_moving_to_growth_mid_period_bills_only_the_difference_for_the_rest(
        api, tenant_factory, owner_headers):
    both, tenant = _starter(api, tenant_factory, owner_headers, units=2)
    assert api.post("/api/contracts", headers=both, json={"term_years": 1}).status_code == 201
    run_billing()                                   # period 0, on Starter
    assert STORE.invoices_for(tenant["tenant_id"])[0].total_usd == STARTER
    api.post("/api/licenses/me/plan", headers=both, json={"plan": "growth"})
    _backdate_period_zero(tenant["tenant_id"], 0.5)

    run_billing()                                   # period 1, on Growth

    invoice = max(STORE.invoices_for(tenant["tenant_id"]), key=lambda i: i.number)
    subscription = [line for line in invoice.lines if line["kind"] == "subscription"]
    arrears = [line for line in invoice.lines if line["kind"] == "arrears"]
    assert subscription[0]["amount_usd"] == SHOP     # the rate card from now on
    [upgrade] = arrears
    assert "Moved from Starter to Growth" in upgrade["description"]
    assert upgrade["amount_usd"] == pytest.approx((SHOP - STARTER) * 0.5, abs=0.02)
    assert invoice.total_usd == pytest.approx(SHOP + (SHOP - STARTER) * 0.5, abs=0.02)


def test_a_second_shop_after_the_move_is_caught_up_on_its_own(
        api, tenant_factory, owner_headers):
    both, tenant = _starter(api, tenant_factory, owner_headers, units=1)
    site = api.post("/api/sites", headers=both, json={"name": "High Street"}).json()
    api.post(f"/api/sites/{site['site_id']}/sensors", headers=both, json={"sensor_id": "CAFE-1"})
    assert api.post("/api/contracts", headers=both, json={"term_years": 1}).status_code == 201
    run_billing()
    api.post("/api/licenses/me/plan", headers=both, json={"plan": "growth"})
    second = api.post("/api/sites", headers=both, json={"name": "Market Square"}).json()
    api.post("/api/licenses/me/sensors", headers=both, json={
        "sensor_id": "MKT-1", "industry_vertical": "restaurant", "location_name": "Fridge"})
    api.post(f"/api/sites/{second['site_id']}/sensors", headers=both, json={"sensor_id": "MKT-1"})
    opened, closed, moved = _backdate_period_zero(tenant["tenant_id"], 0.5)
    late = STORE.get_sensor("MKT-1")
    late.registered_at = opened + (closed - opened) * 0.75
    STORE._db.put("sensor", late.sensor_id, late.to_row())

    run_billing()

    invoice = max(STORE.invoices_for(tenant["tenant_id"]), key=lambda i: i.number)
    arrears = {line["description"].split(":")[0]: line for line in invoice.lines
               if line["kind"] == "arrears"}
    upgrade = next(v for k, v in arrears.items() if k.startswith("Moved from Starter"))
    shop = next(v for k, v in arrears.items() if "added mid-period" in k)
    # The upgrade is priced on the one shop it had when it moved...
    assert upgrade["amount_usd"] == pytest.approx((SHOP - STARTER) * 0.5, abs=0.02)
    # ...and the new shop on the quarter it was open.
    assert shop["amount_usd"] == pytest.approx(SHOP * 0.25, abs=0.02)


def test_a_period_spent_on_starter_owes_nothing_for_units_added(
        api, tenant_factory, owner_headers):
    both, tenant = _starter(api, tenant_factory, owner_headers, units=1)
    assert api.post("/api/contracts", headers=both, json={"term_years": 1}).status_code == 201
    run_billing()
    api.post("/api/licenses/me/sensors", headers=both, json={
        "sensor_id": "CAFE-2", "industry_vertical": "restaurant", "location_name": "Freezer"})
    opened, closed, _ = _backdate_period_zero(tenant["tenant_id"])
    added = STORE.get_sensor("CAFE-2")
    added.registered_at = opened + (closed - opened) / 2
    STORE._db.put("sensor", added.sensor_id, added.to_row())

    run_billing()

    invoice = max(STORE.invoices_for(tenant["tenant_id"]), key=lambda i: i.number)
    assert [line for line in invoice.lines if line["kind"] == "arrears"] == []
    assert invoice.total_usd == STARTER


def test_the_move_is_dated_when_it_happens():
    """The plan change row the bill reads is written by the store itself,
    for an operator-led change as much as a self-service one."""
    tenant = STORE.create_tenant(company_name="X", contact_name="Y", contact_phone="+15550100",
                                 contact_email="x@example.com", plan="starter")
    before = utc_now()
    STORE.change_plan(tenant, "growth", extend_term=False)
    [change] = STORE.plan_changes(tenant.tenant_id)
    at = datetime.fromisoformat(change["at"].replace("Z", "+00:00"))
    assert at >= before.replace(microsecond=0)
    STORE.change_plan(tenant, "growth", extend_term=False)   # no change, no row
    assert len(STORE.plan_changes(tenant.tenant_id)) == 1
