"""The owner's new prices, end to end.

Starter $59 a month (1 location, up to 3 units). Restaurants $149 a month
per location and pharmacies $199 a month per pharmacy, each for up to ten
units at that location, with $15 a month for each unit past ten there.
No setup fee anywhere. The Kitchen Safety Bundle stays +$29 a month per
location and now includes OmniArmor Pro for that location. Every other
trade is "Custom pricing — ask for a quote" on every public page, and its
rate stays in the code so the invoices of businesses already on it keep
working. A business already on the old prices pays the new ones from its
next invoice, and never more than it did for a location.
"""

from __future__ import annotations

import html
import re
from datetime import timedelta

import pytest

import legal
import pricing
from store import STORE

ADMIN = {"X-CyberLogix-Admin": "test-admin-key"}
CUSTOM = [v for v in pricing.PRICE_BOOK if pricing.custom_quote(v)]


def _money(amount: float) -> str:
    return f"${amount:,.0f}"


def _tenant_id(api, headers):
    return api.get("/api/licenses/me", headers=headers).json()["tenant_id"]


def _issue(api, headers, **params):
    body = {"include_add_ons": "", "include_setup": False, "period_days": 30}
    body.update(params)
    resp = api.post("/api/invoices", params={"tenant_id": _tenant_id(api, headers)},
                    headers=ADMIN, json=body)
    assert resp.status_code == 201, resp.text
    return resp.json()


def _site(api, headers, name):
    made = api.post("/api/sites", headers=headers, json={"name": name}).json()
    return (made.get("site") or made)["site_id"]


def _units(api, headers, sensor_factory, count, prefix, vertical="restaurant", site=None):
    for n in range(count):
        sid = f"{prefix}-{n}"
        sensor_factory(headers, sensor_id=sid, vertical=vertical, location=f"Unit {n}")
        if site:
            resp = api.post(f"/api/sites/{site}/sensors", headers=headers,
                            json={"sensor_id": sid})
            assert resp.status_code == 200, resp.text


def _bill(api, headers):
    return api.get("/api/billing", headers=headers).json()


def _signed_up_before_the_change(tenant_id):
    tenant = STORE.get_tenant(tenant_id)
    tenant.activated_at = pricing.PRICES_CHANGED_AT - timedelta(days=90)
    STORE._db.put("tenant", tenant.tenant_id, tenant.to_row())


def _words(page: str) -> str:
    return html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", page)))


# ---- the prices ------------------------------------------------------------


def test_the_owners_prices():
    assert pricing.STARTER_MONTHLY_USD == 59.0
    assert pricing.PRICE_BOOK["restaurant"]["monthly_usd"] == 149.0
    assert pricing.PRICE_BOOK["pharmacy"]["monthly_usd"] == 199.0
    assert pricing.per_site("restaurant") and pricing.per_site("pharmacy")
    assert pricing.UNITS_PER_LOCATION == 10
    assert pricing.EXTRA_UNIT_MONTHLY_USD == 15.0
    assert pricing.SETUP_FEE_PER_SITE_USD == 0.0
    assert pricing.ADD_ONS["kitchen_safety_bundle"]["monthly_usd"] == 29.0
    assert pricing.bundle_price_label() == "+$29 a month per location"
    # Starter is unchanged apart from its price.
    from domain import PLAN_TIERS

    assert PLAN_TIERS["starter"]["max_sensors"] == 3
    assert PLAN_TIERS["starter"]["max_locations"] == 1
    assert pricing.STARTER_ADD_ONS == (pricing.BUNDLE_ADD_ON,)


def test_exactly_the_ten_other_trades_are_priced_by_quote():
    assert sorted(CUSTOM) == sorted([
        "cybersecurity", "private_aviation", "superyacht", "solar_infrastructure",
        "medical_lab", "cryostorage", "country_club", "logistics", "wine_and_art",
        "cannabis"])
    for vertical in CUSTOM:
        # Kept, because the invoices of businesses already on it need it.
        assert pricing.PRICE_BOOK[vertical]["monthly_usd"] > 0
        assert pricing.public_price(vertical) is None
        assert pricing.public_price_label(vertical) == "Custom pricing — ask for a quote"


def test_the_front_page_quotes_the_lowest_published_price(api):
    facts = pricing.headline()
    assert facts["from_label"] == "$149" and facts["from_unit"] == "location"
    page = _words(api.get("/").text)
    # The lowest price anybody can pay is Starter's flat price, and the
    # front page, the plans and the console all start from it.
    assert f"From {facts['starter_label']} a month, no setup fee." in page


# ---- units at one location -------------------------------------------------


def test_ten_units_at_a_location_are_the_location_price(api, operator_factory, sensor_factory):
    headers, _, _ = operator_factory()
    _units(api, headers, sensor_factory, 10, "FRZ")
    bill = _bill(api, headers)
    assert bill["monthly_total_usd"] == 149.0
    assert len(bill["line_items"]) == 1


def test_the_eleventh_unit_is_fifteen_dollars_on_its_own_line(
        api, operator_factory, sensor_factory):
    headers, _, _ = operator_factory()
    _units(api, headers, sensor_factory, 12, "FRZ")
    bill = _bill(api, headers)

    assert bill["monthly_total_usd"] == 149.0 + 2 * 15.0
    location, extra = bill["line_items"]
    assert location["description"] == "1 location (12 sensors)"
    assert location["line_total_usd"] == 149.0
    assert extra["extra_units"] is True
    assert extra["units"] == 2 and extra["unit_price_usd"] == 15.0
    assert extra["line_total_usd"] == 30.0
    assert extra["description"] == "2 extra units past 10 at a location, $15 each"
    # And the invoice says the same.
    invoice = _issue(api, headers)
    assert invoice["total_usd"] == 179.0
    assert any("2 extra units past 10" in line["description"] for line in invoice["lines"])


def test_the_allowance_is_per_location_not_per_business(api, operator_factory, sensor_factory):
    """Eleven at one shop and nine at another: one extra unit, not ten."""
    headers, _, _ = operator_factory()
    _units(api, headers, sensor_factory, 11, "A", site=_site(api, headers, "Boca"))
    _units(api, headers, sensor_factory, 9, "B", site=_site(api, headers, "Boynton"))
    bill = _bill(api, headers)
    assert bill["monthly_total_usd"] == 2 * 149.0 + 15.0
    assert bill["line_items"][1]["units"] == 1


def test_a_unit_not_filed_under_a_location_is_not_counted_past_its_allowance(
        api, operator_factory, sensor_factory):
    """With locations set up, an unfiled unit is not put at either one:
    the customer gets the benefit of the doubt, as for the location count."""
    headers, _, _ = operator_factory()
    _units(api, headers, sensor_factory, 10, "A", site=_site(api, headers, "Boca"))
    _units(api, headers, sensor_factory, 3, "LOOSE")
    bill = _bill(api, headers)
    assert bill["monthly_total_usd"] == 149.0


def test_a_pharmacy_is_one_price_for_its_fridges(api, operator_factory, sensor_factory):
    headers, _, _ = operator_factory()
    _units(api, headers, sensor_factory, 3, "VAX", vertical="pharmacy")
    assert _bill(api, headers)["monthly_total_usd"] == 199.0
    _units(api, headers, sensor_factory, 9, "MORE", vertical="pharmacy")
    assert _bill(api, headers)["monthly_total_usd"] == 199.0 + 2 * 15.0


def test_quote_monthly_and_the_bill_agree_on_extra_units(api, operator_factory, sensor_factory):
    headers, _, _ = operator_factory()
    _units(api, headers, sensor_factory, 13, "FRZ")
    assert _bill(api, headers)["monthly_total_usd"] == pricing.quote_monthly(
        {"restaurant": 1}, units_at_each_location={"restaurant": 13}) == 194.0


def test_the_chain_quote_counts_units_past_the_allowance(api, operator_factory):
    headers, _, _ = operator_factory()
    quote = api.get("/api/v1/enterprise-billing/quote?industry_vertical=restaurant"
                    "&total_branch_locations=3&units_per_branch=12", headers=headers).json()
    assert quote["per_unit"]["extra_units"] == 6
    assert quote["per_unit"]["monthly_usd"] == 3 * 149.0 + 6 * 15.0


def test_extra_units_charge_is_pure_and_simple():
    charge = pricing.extra_units_charge
    assert charge("restaurant", [10, 11, 25]) == {"units": 16, "amount_usd": 240.0, "held": False}
    assert charge("restaurant", []) == {"units": 0, "amount_usd": 0.0, "held": False}
    # A trade with no per-location allowance never has extra units.
    assert charge("cybersecurity", [40])["amount_usd"] == 0.0


# ---- businesses already on the old prices ----------------------------------


def test_an_existing_business_pays_the_new_price_from_its_next_invoice(
        api, operator_factory, sensor_factory, monkeypatch):
    headers, tenant, _ = operator_factory()
    _signed_up_before_the_change(tenant["tenant_id"])
    _units(api, headers, sensor_factory, 3, "FRZ")

    # The invoice it was sent at the old price...
    monkeypatch.setitem(pricing.PRICE_BOOK["restaurant"], "monthly_usd", 249.0)
    old = _issue(api, headers)
    assert old["total_usd"] == 249.0
    monkeypatch.setitem(pricing.PRICE_BOOK["restaurant"], "monthly_usd", 149.0)

    # ...stays as it was sent, and the next one is at the new price.
    new = _issue(api, headers)
    assert new["total_usd"] == 149.0
    assert STORE.get_invoice(old["invoice_id"]).total_usd == 249.0


def test_an_existing_shop_with_many_units_never_pays_more_than_it_did(
        api, operator_factory, sensor_factory):
    """Twenty units at one shop was $249 flat. On the new card it would be
    $149 + 10 x $15 = $299, so for a business that signed up before the
    change the extra units are held at $100: the shop costs $249, never
    more."""
    old_headers, old_tenant, _ = operator_factory(company_name="Old Grill",
                                                  email="old@example.com")
    _signed_up_before_the_change(old_tenant["tenant_id"])
    _units(api, old_headers, sensor_factory, 20, "OLD")
    held = _bill(api, old_headers)
    assert held["monthly_total_usd"] == 249.0
    assert "held so no location costs more than its old $249" in held["line_items"][1]["description"]

    new_headers, _, _ = operator_factory(company_name="New Grill", email="new@example.com")
    _units(api, new_headers, sensor_factory, 20, "NEW")
    assert _bill(api, new_headers)["monthly_total_usd"] == 299.0


def test_an_existing_shop_with_a_few_extra_units_pays_the_lower_new_price(
        api, operator_factory, sensor_factory):
    headers, tenant, _ = operator_factory()
    _signed_up_before_the_change(tenant["tenant_id"])
    _units(api, headers, sensor_factory, 12, "FRZ")
    # $179 is less than the old $249, so nothing is held.
    bill = _bill(api, headers)
    assert bill["monthly_total_usd"] == 179.0
    assert "held" not in bill["line_items"][1]["description"]


def test_an_existing_starter_business_pays_59_from_its_next_invoice(
        api, tenant_factory, sensor_factory):
    headers, tenant = tenant_factory(plan="starter")
    _signed_up_before_the_change(tenant["tenant_id"])
    sensor_factory(headers, sensor_id="CAFE-1", vertical="restaurant", location="Fridge")
    assert _issue(api, headers)["total_usd"] == 59.0


# ---- no setup fee, anywhere -------------------------------------------------


def test_no_invoice_carries_a_setup_line(api, operator_factory, sensor_factory):
    headers, _, _ = operator_factory()
    _site(api, headers, "Boca")
    _units(api, headers, sensor_factory, 2, "FRZ")
    invoice = _issue(api, headers, include_setup=True)
    assert [line for line in invoice["lines"] if line["kind"] == "setup"] == []
    assert invoice["total_usd"] == 149.0


def test_a_signed_contract_first_bill_has_no_setup_line(
        api, tenant_factory, owner_headers, sensor_factory):
    from contracts import run_billing

    headers, tenant = tenant_factory(plan="growth")
    owner = owner_headers(headers)
    sensor_factory(headers, sensor_id="FRZ-1", vertical="restaurant", location="Walk-in")
    api.post("/api/contracts", headers={**headers, **owner}, json={"term_years": 1})
    run_billing()
    (first,) = STORE.invoices_for(tenant["tenant_id"])
    assert not [line for line in first.lines if line["kind"] == "setup"]
    assert first.total_usd == 149.0


@pytest.mark.parametrize("path", ["/pricing", "/help", "/", "/for/restaurant/sheet",
                                  "/for/superyacht/sheet", "/for/pharmacy/sheet"])
def test_no_public_page_mentions_a_setup_fee(api, path):
    words = _words(api.get(path).text)
    assert "$1,500" not in words
    assert "setup charge" not in words.lower()
    assert "commissioning" not in words.lower()
    assert not re.search(r"setup fee of \$[1-9]", words, re.I)


def test_no_agreement_quotes_a_setup_fee_or_a_price():
    """The agreements say "Prices are on your order" and quote none, so
    the price change needed no new version of them. (2.5 is for how paying
    yearly and cancelling work, which are terms, not prices; 2.6 for who
    sees what with insurance partners and proof reports; 2.7 for what a
    repair company gets from a repair quote; 2.8 for Pilot and a business's
    own AI key.)"""
    for slug in legal.DOCUMENTS:
        body = legal.document(slug)["body_markdown"]
        assert "setup" not in body.lower() and "commissioning" not in body.lower()
        for entry in pricing.PRICE_BOOK.values():
            assert _money(entry["monthly_usd"]) not in body
        assert "$1,500" not in body and "$79" not in body and "$59" not in body
    assert legal.TERMS_VERSION == "2.8"


def test_the_docs_say_there_is_no_setup_fee():
    from pathlib import Path

    billing = (Path(__file__).resolve().parent.parent / "docs" / "BILLING.md").read_text()
    assert "$1,500 per site" not in billing
    assert "No setup fee" in billing


# ---- the trades priced by quote --------------------------------------------


@pytest.mark.parametrize("path", ["/", "/pricing", "/help", "/api/industries",
                                  "/api/billing/pricing", "/api/for/cybersecurity",
                                  "/for/cybersecurity/sheet", "/for/restaurant/sheet",
                                  "/for/logistics/sheet"])
def test_no_public_page_or_catalogue_shows_a_custom_trades_rate(api, path):
    text = api.get(path).text
    for vertical in CUSTOM:
        rate = pricing.PRICE_BOOK[vertical]["monthly_usd"]
        assert _money(rate) not in text, (path, vertical)
        assert f'"monthly_usd":{rate}' not in text.replace(" ", ""), (path, vertical)


def test_the_catalogues_say_custom_pricing(api):
    for path, key in (("/api/industries", "industries"), ("/api/billing/pricing", "pricing")):
        rows = {r["vertical"]: r for r in api.get(path).json()[key]}
        for vertical in CUSTOM:
            assert rows[vertical]["monthly_usd"] is None
            assert rows[vertical]["custom_pricing"] is True
            assert rows[vertical]["price_label"] == pricing.CUSTOM_PRICE_LABEL


def test_the_industry_page_says_custom_and_offers_to_talk(api, monkeypatch):
    import helpdesk

    monkeypatch.setattr(helpdesk, "SUPPORT_EMAIL", "sales@cyberlogix.example")
    body = api.get("/api/for/superyacht").json()
    assert body["price_custom"] is True
    assert body["price_label"] == pricing.CUSTOM_PRICE_LABEL
    assert body["talk_href"].startswith("mailto:sales@cyberlogix.example")
    assert body["talk_address"] == "sales@cyberlogix.example"
    restaurant = api.get("/api/for/restaurant").json()
    assert restaurant["price_label"] == "$149 a month per location"
    assert "each one past 10 is $15 a month" in restaurant["price_note"]


def test_the_landing_page_cards_say_custom_with_a_way_to_talk(api, monkeypatch):
    from pathlib import Path

    import helpdesk

    monkeypatch.setattr(helpdesk, "SUPPORT_EMAIL", "sales@cyberlogix.example")
    page = api.get("/").text
    assert 'data-talk-href="mailto:sales@cyberlogix.example?subject=' in page
    assert 'data-talk-address="sales@cyberlogix.example"' in page
    script = (Path(__file__).resolve().parent.parent / "static" / "landing.js").read_text()
    assert "Custom pricing" in script and "Ask for a quote" in script
    assert "talk-address" in script


def test_a_custom_customer_is_still_invoiced_from_the_code(api, operator_factory, sensor_factory):
    headers, _, _ = operator_factory()
    sensor_factory(headers, sensor_id="RACK-1", vertical="cybersecurity")
    sensor_factory(headers, sensor_id="RACK-2", vertical="cybersecurity")
    sensor_factory(headers, sensor_id="TANK-1", vertical="cryostorage")
    expected = (2 * pricing.PRICE_BOOK["cybersecurity"]["monthly_usd"]
                + pricing.PRICE_BOOK["cryostorage"]["monthly_usd"])
    assert _bill(api, headers)["monthly_total_usd"] == expected
    invoice = _issue(api, headers)
    assert invoice["total_usd"] == expected


def test_the_seat_limit_does_not_quote_a_custom_rate(api, tenant_factory, sensor_factory):
    headers, _ = tenant_factory(plan="trial")
    for n in range(5):
        sensor_factory(headers, sensor_id=f"RACK-{n}", vertical="cybersecurity")
    refused = api.post("/api/licenses/me/sensors", headers=headers, json={
        "sensor_id": "RACK-9", "industry_vertical": "cybersecurity",
        "location_name": "Hall"})
    detail = str(refused.json()["detail"])
    assert "$899" not in detail
    assert "by quote" in detail


# ---- the bundle's wording ---------------------------------------------------


def test_the_bundle_includes_omniarmor_pro_wherever_it_is_described(api):
    entry = pricing.ADD_ONS["kitchen_safety_bundle"]
    assert entry["includes"] == "OmniArmor Pro for that location"
    assert "OmniArmor Pro for that location included" in entry["description"]
    assert pricing.bundle_includes_label() == "Includes OmniArmor Pro for that location"

    pricing_page = _words(api.get("/pricing").text)
    assert "+$29 a month per location" in pricing_page
    assert "Includes OmniArmor Pro for that location." in pricing_page
    help_page = _words(api.get("/help").text)
    assert "includes OmniArmor Pro for that location" in help_page
    sheet = _words(api.get("/for/restaurant/sheet").text)
    assert "Kitchen Safety Bundle: $29 a month per location, includes OmniArmor Pro" in sheet
