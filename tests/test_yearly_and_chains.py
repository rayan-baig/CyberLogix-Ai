"""Paying yearly (ten months for twelve) and the chain discounts.

The rules, as the owner set them and pricing.py holds them:

* Yearly costs ten times the monthly price and covers twelve months, on
  everything recurring: Starter, the location prices, extra units and the
  extras. One invoice for the year; anything added part-way through is a
  prorated invoice for the rest of that year; reminders 30 and 7 days
  before renewal; monthly and yearly can be swapped at renewal.
* Restaurants and pharmacies take 10% off each location from 5 locations
  and 20% off from 20, on its own invoice line, never on Starter or on the
  extra-unit fee, and yearly is worked out on the discounted price.
* That is the only volume discount: enterprise contracts use the same
  tiers, and a business on one is billed from the contract alone.
* Cancelling bills nothing more, refunds nothing, and the service runs to
  the end of what was paid for.
"""

import json
import re
import shutil
import subprocess
from datetime import timedelta
from pathlib import Path

import pytest

import pricing
from contracts import bill_mid_year_changes, run_billing, run_renewal_reminders
from store import STORE, add_months, utc_now

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "static" / "pricing.js"
SHOP = pricing.PRICE_BOOK["restaurant"]["monthly_usd"]
PHARMACY = pricing.PRICE_BOOK["pharmacy"]["monthly_usd"]


# ---- helpers ----------------------------------------------------------------


def _estate(tenant_id, restaurants=0, pharmacies=0, units_each=1):
    """Locations as sites, each with `units_each` sensors filed under it."""
    n = 0
    for vertical, count in (("restaurant", restaurants), ("pharmacy", pharmacies)):
        for index in range(count):
            site = STORE.create_site(tenant_id, f"{vertical} {index}")
            for unit in range(units_each):
                n += 1
                sensor = STORE.register_sensor(f"{vertical[:3].upper()}-{index}-{unit}",
                                               tenant_id, vertical, site.name)
                STORE.assign_sensor_to_site(sensor, site.site_id)
    return n


def _business(tenant_factory, owner_headers, plan="enterprise", name="Harbor Group",
              email="owner@harbor.example"):
    key, tenant = tenant_factory(plan=plan, company_name=name)
    owner = owner_headers(key, email=email)
    return {**key, **owner}, tenant["tenant_id"]


def _sign(api, headers, **body):
    # No escalator unless a test asks for one, so a second year's figures
    # are the rate card's.
    payload = {"term_years": 1, "escalator_percent": 0.0}
    payload.update(body)
    resp = api.post("/api/contracts", headers=headers, json=payload)
    assert resp.status_code == 201, resp.text
    return resp.json()


def _invoices(tenant_id):
    return sorted((i for i in STORE.invoices_for(tenant_id) if i.state != "void"),
                  key=lambda i: i.number)


def _bill(api, headers):
    return api.get("/api/billing", headers=headers).json()


def _quote_js(cases):
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    harness = (f"const calc = require({json.dumps(str(SCRIPT))});\n"
               f"console.log(JSON.stringify({json.dumps(cases)}.map((c) => calc.quote(c))));\n")
    done = subprocess.run([node, "-e", harness], capture_output=True, text=True, timeout=20)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


# ---- yearly is ten times monthly ---------------------------------------------


def test_yearly_is_ten_times_monthly_for_every_plan_and_add_on():
    assert pricing.YEARLY_MONTHS_CHARGED == 10
    assert pricing.YEARLY_MONTHS_COVERED == 12
    assert pricing.yearly_label() == "2 months free"
    cases = {
        "Starter": (pricing.quote_monthly({"restaurant": 1}, plan="starter"), 59.0),
        "Restaurant": (pricing.quote_monthly({"restaurant": 1}), 149.0),
        "Pharmacy": (pricing.quote_monthly({"pharmacy": 1}), 199.0),
        "Extra unit": (pricing.quote_monthly({"restaurant": 1},
                                             units_at_each_location={"restaurant": 11})
                       - SHOP, 15.0),
        "Bundle": (pricing.add_on_price(pricing.BUNDLE_ADD_ON, 1, 1), 29.0),
    }
    for name, (monthly, card) in cases.items():
        assert monthly == card, name
        assert pricing.yearly_price(monthly) == round(card * 10, 2), name
    assert pricing.quote_monthly({"restaurant": 1}, plan="starter",
                                 add_ons=[pricing.BUNDLE_ADD_ON], yearly=True) == 880.0
    assert pricing.quote_monthly({"pharmacy": 1}, yearly=True) == 1990.0
    for entry in pricing.ADD_ONS.values():
        assert pricing.yearly_price(entry["monthly_usd"]) == entry["monthly_usd"] * 10


def test_a_yearly_invoice_is_ten_months_of_everything(api, tenant_factory, owner_headers,
                                                       monkeypatch):
    """One location with twelve units (two extra) and the bundle, yearly."""
    headers, tenant_id = _business(tenant_factory, owner_headers)
    _estate(tenant_id, restaurants=1, units_each=12)
    monkeypatch.setattr(pricing, "auto_add_ons", lambda tenant: [pricing.BUNDLE_ADD_ON])
    _sign(api, headers, annual_prepay=True)
    run_billing()

    (invoice,) = _invoices(tenant_id)
    monthly = SHOP + 2 * pricing.EXTRA_UNIT_MONTHLY_USD + 29.0
    assert invoice.total_usd == pytest.approx(monthly * 10)
    kinds = [line["kind"] for line in invoice.lines]
    assert kinds.count("discount") == 1
    free = [line for line in invoice.lines if line["kind"] == "discount"][0]
    assert free["amount_usd"] == pytest.approx(-monthly * 2)
    assert invoice.period_days in (365, 366)


def test_a_starter_yearly_invoice_is_590(api, tenant_factory, owner_headers):
    headers, tenant_id = _business(tenant_factory, owner_headers, plan="starter")
    for n in range(2):
        STORE.register_sensor(f"FRZ-{n}", tenant_id, "restaurant", "Cafe")
    _sign(api, headers, annual_prepay=True)
    run_billing()
    (invoice,) = _invoices(tenant_id)
    assert invoice.total_usd == pricing.STARTER_MONTHLY_USD * 10 == 590.0


# ---- chain tiers --------------------------------------------------------------


@pytest.mark.parametrize("locations,percent,monthly", [
    (4, 0.0, 4 * SHOP),
    (5, 10.0, 670.5),
    (19, 10.0, 2384.0),      # held to the 20-location price
    (20, 20.0, 2384.0),
    (50, 20.0, 5960.0),
])
def test_chain_tiers(api, tenant_factory, locations, percent, monthly):
    headers, tenant = tenant_factory()
    _estate(tenant["tenant_id"], restaurants=locations)
    assert pricing.chain_discount_percent(locations) == percent

    bill = _bill(api, headers)
    assert bill["monthly_total_usd"] == pytest.approx(monthly)
    assert bill["yearly_total_usd"] == pytest.approx(monthly * 10)
    chain = [line for line in bill["line_items"] if line.get("chain_discount")]
    if not percent:
        assert chain == []
        return
    (line,) = chain
    assert line["description"].startswith(
        f"Chain discount ({locations} locations): −{percent:g}%")
    assert line["line_total_usd"] == pytest.approx(monthly - locations * SHOP)
    assert pricing.quote_monthly({"restaurant": locations}) == pytest.approx(monthly)


def test_the_owners_example_is_computed():
    example = pricing.chain_example("restaurant", 20)
    assert (example["list_usd"], example["each_usd"]) == (149.0, 119.2)
    assert example["monthly_usd"] == 2384.0
    assert example["yearly_usd"] == 23840.0


def test_the_chain_line_reads_as_promised(api, tenant_factory, owner_headers):
    headers, tenant_id = _business(tenant_factory, owner_headers)
    _estate(tenant_id, restaurants=20)
    _sign(api, headers)
    run_billing()
    (invoice,) = _invoices(tenant_id)
    (chain,) = [line for line in invoice.lines if line["kind"] == "chain_discount"]
    assert chain["description"] == "Chain discount (20 locations): −20%"
    assert chain["amount_usd"] == -596.0
    assert invoice.total_usd == 2384.0


def test_restaurants_and_pharmacies_count_together(api, tenant_factory):
    headers, tenant = tenant_factory()
    _estate(tenant["tenant_id"], restaurants=12, pharmacies=8)
    bill = _bill(api, headers)
    gross = 12 * SHOP + 8 * PHARMACY
    assert bill["monthly_total_usd"] == pytest.approx(round(gross * 0.8, 2))


def test_the_discount_is_not_on_extra_units_or_starter(api, tenant_factory):
    headers, tenant = tenant_factory()
    _estate(tenant["tenant_id"], restaurants=5, units_each=12)  # 2 extra at each
    bill = _bill(api, headers)
    extra = 5 * 2 * pricing.EXTRA_UNIT_MONTHLY_USD
    assert bill["monthly_total_usd"] == pytest.approx(5 * SHOP * 0.9 + extra)

    starter_headers, starter = tenant_factory(plan="starter", company_name="Cafe")
    STORE.register_sensor("CAFE-1", starter["tenant_id"], "restaurant", "Cafe")
    bill = _bill(api, starter_headers)
    assert bill["monthly_total_usd"] == pricing.STARTER_MONTHLY_USD
    assert not [line for line in bill["line_items"] if line.get("chain_discount")]


def test_chain_and_yearly_stack(api, tenant_factory, owner_headers):
    """Yearly is ten months of the discounted price: $23,840 for 20 shops."""
    headers, tenant_id = _business(tenant_factory, owner_headers)
    _estate(tenant_id, restaurants=20)
    _sign(api, headers, annual_prepay=True)
    run_billing()
    (invoice,) = _invoices(tenant_id)
    assert invoice.total_usd == 23840.0
    by_kind = {line["kind"]: line for line in invoice.lines}
    assert by_kind["subscription"]["amount_usd"] == 20 * SHOP * 12
    assert by_kind["chain_discount"]["amount_usd"] == -596.0 * 12
    assert by_kind["chain_discount"]["description"].startswith(
        "Chain discount (20 locations): −20%")
    assert by_kind["discount"]["amount_usd"] == -2384.0 * 2
    assert pricing.quote_monthly({"restaurant": 20}, yearly=True) == invoice.total_usd


# ---- mid-year additions -----------------------------------------------------------


def _yearly_half_way(api, tenant_factory, owner_headers, restaurants=2):
    headers, tenant_id = _business(tenant_factory, owner_headers)
    _estate(tenant_id, restaurants=restaurants)
    _sign(api, headers, annual_prepay=True)
    run_billing()
    sub = STORE.active_subscription(tenant_id)
    # Six months into the paid year.
    sub.started_at = add_months(sub.started_at, -6)
    STORE.save_subscription(sub)
    return headers, tenant_id, sub


def test_a_location_added_mid_year_is_prorated(api, tenant_factory, owner_headers):
    headers, tenant_id, sub = _yearly_half_way(api, tenant_factory, owner_headers)
    site = STORE.create_site(tenant_id, "New shop")
    sensor = STORE.register_sensor("NEW-1", tenant_id, "restaurant", "New shop")
    STORE.assign_sensor_to_site(sensor, site.site_id)

    now = utc_now()
    result = run_billing(now)
    assert result["invoices_issued"] == 1
    extra = _invoices(tenant_id)[-1]
    end = sub.period_start(1)
    days_left = (end.date() - now.date()).days
    period_days = (end - sub.period_start(0)).days
    assert extra.total_usd == round(SHOP * 10 * days_left / period_days, 2)
    (line,) = extra.lines
    assert line["kind"] == "mid_year"
    assert "2 to 3 locations" in line["description"]
    assert f"{days_left} days left" in line["description"]
    assert extra.billing_period is None and extra.source.startswith("midyear:")

    # Charged once: the next pass finds nothing new.
    assert run_billing(now)["invoices_issued"] == 0


def test_units_added_mid_year_are_prorated_and_removals_refund_nothing(
        api, tenant_factory, owner_headers):
    headers, tenant_id, sub = _yearly_half_way(api, tenant_factory, owner_headers,
                                               restaurants=1)
    site = STORE.sites_for(tenant_id)[0]
    added = []
    for n in range(11):        # 12 units at the shop: two past the ten
        sensor = STORE.register_sensor(f"MORE-{n}", tenant_id, "restaurant", site.name)
        STORE.assign_sensor_to_site(sensor, site.site_id)
        added.append(sensor)
    now = utc_now()
    invoice = bill_mid_year_changes(STORE.get_tenant(tenant_id), sub, now)
    end = sub.period_start(1)
    fraction = (end.date() - now.date()).days / (end - sub.period_start(0)).days
    assert invoice.total_usd == round(2 * pricing.EXTRA_UNIT_MONTHLY_USD * 10 * fraction, 2)

    # Take one off and put one back: no refund, and no second charge.
    STORE.assign_sensor_to_site(added[-1], None)
    assert bill_mid_year_changes(STORE.get_tenant(tenant_id), sub, now) is None
    STORE.assign_sensor_to_site(added[-1], site.site_id)
    assert bill_mid_year_changes(STORE.get_tenant(tenant_id), sub, now) is None


def test_a_monthly_plan_has_no_mid_year_invoice(api, tenant_factory, owner_headers):
    headers, tenant_id = _business(tenant_factory, owner_headers)
    _estate(tenant_id, restaurants=2)
    _sign(api, headers)
    run_billing()
    site = STORE.create_site(tenant_id, "Third")
    sensor = STORE.register_sensor("THIRD-1", tenant_id, "restaurant", "Third")
    STORE.assign_sensor_to_site(sensor, site.site_id)
    sub = STORE.active_subscription(tenant_id)
    assert bill_mid_year_changes(STORE.get_tenant(tenant_id), sub) is None


# ---- renewal reminders ----------------------------------------------------------


def _yearly_one_shop(api, tenant_factory, owner_headers):
    headers, tenant_id = _business(tenant_factory, owner_headers)
    _estate(tenant_id, restaurants=1)
    _sign(api, headers, annual_prepay=True)
    run_billing()
    sub = STORE.active_subscription(tenant_id)
    return headers, tenant_id, sub


def _reminders():
    return [m for m in STORE.mail_log(limit=500) if m.dedupe_key.startswith("renewal:")]


def test_renewal_reminders_are_sent_once_each(api, tenant_factory, owner_headers):
    _, tenant_id, sub = _yearly_one_shop(api, tenant_factory, owner_headers)
    renews = sub.period_start(1)

    assert run_renewal_reminders(renews - timedelta(days=45))["sent"] == 0
    assert run_renewal_reminders(renews - timedelta(days=29, hours=12))["sent"] == 1
    assert run_renewal_reminders(renews - timedelta(days=20))["sent"] == 0
    assert run_renewal_reminders(renews - timedelta(days=6, hours=12))["sent"] == 1
    assert run_renewal_reminders(renews - timedelta(days=1))["sent"] == 0

    sent = _reminders()
    assert sorted(m.dedupe_key.rsplit(":", 1)[1] for m in sent) == ["30", "7"]
    thirty = next(m for m in sent if m.dedupe_key.endswith(":30"))
    assert "renews on" in thirty.subject
    assert "$1,490.00" in thirty.body              # one shop, ten months
    assert pricing.CANCELLATION_RULE in thirty.body


def test_a_monthly_plan_gets_no_renewal_reminder(api, tenant_factory, owner_headers):
    headers, tenant_id = _business(tenant_factory, owner_headers)
    _estate(tenant_id, restaurants=1)
    _sign(api, headers)
    run_billing()
    sub = STORE.active_subscription(tenant_id)
    assert run_renewal_reminders(sub.period_start(1) - timedelta(days=5))["sent"] == 0
    assert _reminders() == []


# ---- switching at renewal ---------------------------------------------------------


def test_monthly_to_yearly_switches_at_renewal(api, tenant_factory, owner_headers):
    headers, tenant_id = _business(tenant_factory, owner_headers)
    _estate(tenant_id, restaurants=20)
    _sign(api, headers)
    run_billing()
    old = STORE.active_subscription(tenant_id)
    boundary = old.period_start(1)

    resp = api.post("/api/contracts/billing-interval", headers=headers,
                    json={"billing_interval": "yearly"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["switching_to"] == "yearly"
    assert body["next_invoice_usd"] == 23840.0
    assert body["switches_at"] == boundary.strftime("%Y-%m-%dT%H:%M:%SZ")

    # Nothing changes before what was paid for runs out.
    assert run_billing()["invoices_issued"] == 0
    assert STORE.active_subscription(tenant_id).subscription_id == old.subscription_id

    run_billing(boundary + timedelta(hours=1))
    fresh = STORE.active_subscription(tenant_id)
    assert fresh.subscription_id != old.subscription_id
    assert fresh.annual_prepay and fresh.started_at == boundary
    assert STORE.get_subscription(old.subscription_id).cancellation_reason == (
        "switched to yearly billing")
    latest = _invoices(tenant_id)[-1]
    assert latest.total_usd == 23840.0
    assert latest.period_days in (365, 366)


def test_yearly_to_monthly_switches_at_renewal(api, tenant_factory, owner_headers):
    headers, tenant_id = _business(tenant_factory, owner_headers)
    _estate(tenant_id, restaurants=2)
    _sign(api, headers, annual_prepay=True)
    run_billing()
    old = STORE.active_subscription(tenant_id)
    boundary = old.period_start(1)
    api.post("/api/contracts/billing-interval", headers=headers,
             json={"billing_interval": "monthly"})
    assert STORE.active_subscription(tenant_id).public()["switching_to"] == "monthly"

    # Changed their mind, then changed it back.
    api.post("/api/contracts/billing-interval", headers=headers,
             json={"billing_interval": "yearly"})
    assert STORE.active_subscription(tenant_id).next_interval is None
    api.post("/api/contracts/billing-interval", headers=headers,
             json={"billing_interval": "monthly"})

    run_billing(boundary + timedelta(hours=1))
    fresh = STORE.active_subscription(tenant_id)
    assert not fresh.annual_prepay and fresh.started_at == boundary
    assert _invoices(tenant_id)[-1].total_usd == 2 * SHOP


def test_a_renewal_can_switch_interval(api, tenant_factory, owner_headers):
    headers, tenant_id = _business(tenant_factory, owner_headers)
    _estate(tenant_id, restaurants=1)
    _sign(api, headers)
    run_billing()
    resp = api.post("/api/contracts/renew", headers=headers,
                    json={"term_years": 1, "billing_interval": "yearly"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["contract"]["billing_interval"] == "annual"


# ---- cancelling ------------------------------------------------------------------


def test_cancelling_mid_year_refunds_nothing_and_service_runs_to_the_end(
        api, tenant_factory, owner_headers):
    headers, tenant_id, sub = _yearly_half_way(api, tenant_factory, owner_headers)
    paid_to = sub.period_start(1)
    resp = api.post("/api/contracts/cancel", headers=headers, json={"reason": "closing"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["refund_usd"] == 0.0
    assert body["rule"] == pricing.CANCELLATION_RULE
    assert body["service_until"] == paid_to.strftime("%Y-%m-%dT%H:%M:%SZ")
    assert "carries on until" in body["message"]
    before = len(_invoices(tenant_id))

    # Nothing more is billed: not a mid-year addition, not the renewal.
    site = STORE.create_site(tenant_id, "After")
    sensor = STORE.register_sensor("AFTER-1", tenant_id, "restaurant", "After")
    STORE.assign_sensor_to_site(sensor, site.site_id)
    run_billing()
    run_billing(paid_to + timedelta(days=3))
    assert len(_invoices(tenant_id)) == before
    # And no credit or refund line was issued for the rest of the year.
    assert not [line for inv in _invoices(tenant_id) for line in inv.lines
                if line["amount_usd"] < 0 and line["kind"] not in ("discount",)]


def test_the_rule_is_shown_where_people_sign(api, tenant_factory, owner_headers):
    headers, tenant_id = _business(tenant_factory, owner_headers)
    _estate(tenant_id, restaurants=1)
    read = api.get("/api/contracts", headers=headers).json()
    assert read["yearly"]["cancellation"] == pricing.CANCELLATION_RULE
    assert read["yearly"]["yearly_usd"] == 1490.0
    assert _sign(api, headers, annual_prepay=True)["cancellation"] == pricing.CANCELLATION_RULE
    console = (ROOT / "static" / "console.js").read_text()
    assert "yearly.cancellation" in console and "/api/contracts/billing-interval" in console
    page = re.sub(r"\s+", " ", api.get("/pricing").text)
    assert pricing.CANCELLATION_RULE.replace("'", "&#x27;") in page


# ---- one volume discount -------------------------------------------------------


def test_no_double_discount_with_an_enterprise_contract(api, tenant_factory, owner_headers):
    headers, tenant_id = _business(tenant_factory, owner_headers)
    _estate(tenant_id, restaurants=20)
    assert _bill(api, headers)["monthly_total_usd"] == 2384.0

    STORE.provision_contract(tenant_id, "Harbor Group", "restaurant", 20, "cfo@harbor.example")
    bill = _bill(api, headers)
    assert bill["billing_model"] == "enterprise_volume"
    # 20% off once: $2,384, not 20% off $2,384.
    assert bill["monthly_total_usd"] == 2384.0
    assert not [line for line in bill["line_items"] if line.get("chain_discount")]
    assert bill["contract"]["chain_discount_percent"] == 20.0

    _sign(api, headers, annual_prepay=True)
    run_billing()
    (invoice,) = _invoices(tenant_id)
    assert invoice.total_usd == 23840.0
    assert not [line for line in invoice.lines if line["kind"] == "chain_discount"]


def test_the_old_enterprise_ladder_is_gone():
    import domain
    import store

    for name in ("VOLUME_DISCOUNT_STEP_PERCENT", "MAX_VOLUME_DISCOUNT_PERCENT",
                 "calculate_volume_tier_price", "volume_discount_percent"):
        assert not hasattr(domain, name) and not hasattr(store, name), name
    assert pricing.CHAIN_DISCOUNT_TIERS == ((5, 10.0), (20, 20.0))


# ---- the calculator is the invoice ---------------------------------------------


CHAIN_ESTATES = [
    {"restaurant": 4}, {"restaurant": 5}, {"restaurant": 18}, {"restaurant": 19},
    {"restaurant": 20}, {"restaurant": 50}, {"restaurant": 12, "pharmacy": 8},
    {"pharmacy": 7},
]


def test_the_calculator_matches_the_invoice_for_chains(api, tenant_factory):
    page = api.get("/pricing").text
    chain = re.search(r'data-chain="([^"]+)"', page).group(1)
    rates = dict(re.findall(r'<option value="([a-z_]+)" data-rate="([0-9.]+)"', page))
    tiers = [[int(a), float(b)] for a, b in (p.split(":") for p in chain.split(","))]
    cases = [{"starter": None, "extraRate": 15, "locations": 1, "chain": tiers, "addOns": [],
              "lines": [{"rate": float(rates[v]), "count": n, "included": 10, "each": 3}
                        for v, n in units.items()]}
             for units in CHAIN_ESTATES]
    got = _quote_js(cases)
    for index, (units, js) in enumerate(zip(CHAIN_ESTATES, got)):
        expected = pricing.quote_monthly(units)
        assert js == pytest.approx(expected, abs=0.001), units
        headers, tenant = tenant_factory(company_name=f"Chain {index}")
        _estate(tenant["tenant_id"], restaurants=units.get("restaurant", 0),
                pharmacies=units.get("pharmacy", 0), units_each=3)
        assert _bill(api, headers)["monthly_total_usd"] == pytest.approx(expected), units


def test_the_page_has_the_toggle_the_table_and_the_example(api):
    page = re.sub(r"\s+", " ", api.get("/pricing").text)
    assert 'name="calc-pay" value="monthly" checked' in page
    assert 'name="calc-pay" value="yearly"' in page
    assert "Yearly: 2 months free" in page
    # Computed from code, and the figures the owner asked for.
    example = pricing.chain_example("restaurant")
    assert ("20 restaurants: $149 → $119.20 each, $2,384 a month, or $23,840 a year"
            in page)
    assert f"${example['yearly_usd']:,.0f} a year" in page
    rows = re.findall(r'<tr data-chain-from="(\d+)">(.*?)</tr>', page)
    assert [int(r[0]) for r in rows] == [1, 5, 20]
    assert "$134.10" in rows[1][1] and "$119.20" in rows[2][1] and "$1,192" in rows[2][1]
    assert "$179.10" in rows[1][1] and "$159.20" in rows[2][1]
    assert "12 months for the price of 10" in page


def test_the_example_follows_the_code(api, monkeypatch):
    monkeypatch.setattr(pricing, "CHAIN_DISCOUNT_TIERS", ((5, 10.0), (25, 25.0)))
    page = re.sub(r"\s+", " ", api.get("/pricing").text)
    assert "25 restaurants: $149 → $111.75 each" in page
    assert 'data-chain="5:10,25:25"' in page


def test_signup_and_the_price_list_say_it(api):
    sectors = api.get("/api/signup/sectors").json()
    assert sectors["yearly"]["label"] == "2 months free"
    assert "20% off from 20" in sectors["chain_discounts"]
    book = api.get("/api/billing/pricing").json()
    assert [row["percent"] for row in book["chain_discounts"]] == [0.0, 10.0, 20.0]
    assert book["yearly"]["months_charged"] == 10
    assert 'id="pay-options"' in (ROOT / "static" / "signup.html").read_text()
    assert "info.yearly.cancellation" in (ROOT / "static" / "signup.js").read_text()


# ---- existing customers -------------------------------------------------------


def test_existing_monthly_customers_get_the_chain_discount_from_the_next_invoice(
        api, tenant_factory, owner_headers, monkeypatch):
    headers, tenant_id = _business(tenant_factory, owner_headers)
    _estate(tenant_id, restaurants=20)
    # Before chain discounts existed.
    monkeypatch.setattr(pricing, "CHAIN_DISCOUNT_TIERS", ((10 ** 9, 0.0),))
    _sign(api, headers)
    run_billing()
    (first,) = _invoices(tenant_id)
    assert first.total_usd == 20 * SHOP

    monkeypatch.undo()
    sub = STORE.active_subscription(tenant_id)
    run_billing(sub.period_start(1) + timedelta(hours=1))
    second = _invoices(tenant_id)[-1]
    assert second.total_usd == 2384.0
    # The invoice already sent is exactly as it was.
    assert STORE.get_invoice(first.invoice_id).total_usd == 20 * SHOP
    # Still monthly: nobody is moved onto yearly.
    assert not STORE.active_subscription(tenant_id).annual_prepay
    assert not [l for l in second.lines if l["kind"] == "discount"]


def test_an_existing_yearly_customer_is_not_sent_a_surprise_bill(
        api, tenant_factory, owner_headers):
    """A year billed before mid-year tracking existed starts from the
    estate as it is, charging nothing; only what is added after is billed."""
    headers, tenant_id, sub = _yearly_half_way(api, tenant_factory, owner_headers)
    sub.year_covered = {}
    STORE.save_subscription(sub)
    site = STORE.create_site(tenant_id, "Added before tracking")
    sensor = STORE.register_sensor("OLD-1", tenant_id, "restaurant", site.name)
    STORE.assign_sensor_to_site(sensor, site.site_id)
    assert run_billing()["invoices_issued"] == 0
    assert sub.year_covered["monthly_usd"] == 3 * SHOP

    site = STORE.create_site(tenant_id, "Added after")
    sensor = STORE.register_sensor("NEW-2", tenant_id, "restaurant", site.name)
    STORE.assign_sensor_to_site(sensor, site.site_id)
    assert run_billing()["invoices_issued"] == 1


def test_an_old_annual_prepay_renews_at_two_months_free(api, tenant_factory, owner_headers):
    """A contract that took the old 10% prepay is a yearly contract: its
    next year is ten months, which is cheaper than twelve less 10%."""
    headers, tenant_id = _business(tenant_factory, owner_headers)
    _estate(tenant_id, restaurants=1)
    _sign(api, headers, annual_prepay=True, escalator_percent=5.0)
    run_billing()
    sub = STORE.active_subscription(tenant_id)
    run_billing(sub.period_start(1) + timedelta(hours=1))
    renewal = _invoices(tenant_id)[-1]
    rate = SHOP * sub.rate_multiplier(1)          # year two, escalated
    assert renewal.total_usd == pytest.approx(round(rate * 12, 2) - round(rate * 2, 2))
    assert renewal.total_usd < round(rate * 12 * 0.9, 2)
