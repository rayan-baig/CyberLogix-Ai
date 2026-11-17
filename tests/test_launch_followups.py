"""Follow-ups to the pre-launch review, each pinned.

1. Renewing during a pause and then coming back early moves the new
   contract's first bill back too: no free days.
2. A past year's yearly invoice with mid-year additions cannot be voided:
   the re-issue would bill those additions again.
3. The quote at /api/billing/deal says what the contract will bill, year
   by year, for monthly, yearly and multi-year deals.
4. No invoice goes below $0, whatever the discounts on it.
5. A repair lead fee is owed only once the lead email was sent.
6. Pausing is limited to 3 months in any 12.
7. On a yearly plan the offer to stay and the founding member code take
   their percent off what the customer actually pays.
9. A refund takes back only the partner's share of what was refunded,
   and later payments keep earning.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

import founders
import insurers
import pricing
import repairs
import retention
from contracts import run_billing
from store import STORE, add_months, iso, utc_now
from tests import test_insurance_partners, test_pause_and_stay, test_repair_partners
from tests.test_insurance_partners import _join, _pay
from tests.test_repair_partners import _approve, _quote
from tests.test_yearly_and_chains import _business, _estate, _invoices, _sign

SHOP = pricing.PRICE_BOOK["restaurant"]["monthly_usd"]

# Fixtures from the suites these were found beside.
grill = test_pause_and_stay.grill
shop = test_repair_partners.shop
inbox = test_repair_partners.inbox
partner = test_insurance_partners.partner


def _pause(api, headers, months):
    return api.post("/api/leaving/pause", headers=headers, json={"months": months})


def _latest(tenant_id):
    return sorted(STORE.invoices_for(tenant_id), key=lambda i: i.number)[-1]


# ---- 1. renewing during a pause ---------------------------------------------


def test_renewing_while_paused_then_coming_back_early_moves_the_new_contract_too(api, grill):
    tid = grill["tenant_id"]
    old = STORE.active_subscription(tid)
    before = old.period_start(1)
    assert _pause(api, grill["headers"], 3).status_code == 200
    renewed = api.post("/api/contracts/renew", headers=grill["headers"], json={"term_years": 1})
    assert renewed.status_code == 200, renewed.text
    fresh = STORE.active_subscription(tid)
    assert fresh.subscription_id != old.subscription_id
    # Still the whole pause ahead: the new contract's first bill is after it.
    [row] = STORE._db.all(retention.PAUSE_KIND)
    booked = retention._parse(row["resume_at"]) - retention._parse(row["paused_at"])
    assert abs((fresh.period_start(0) - before) - booked) < timedelta(seconds=5)

    # Ten days in, the owner comes back early.
    row["paused_at"] = iso(utc_now() - timedelta(days=10))
    STORE._db.put(retention.PAUSE_KIND, row["pause_id"], row)
    assert api.post("/api/leaving/resume", headers=grill["headers"]).status_code == 200
    fresh = STORE.active_subscription(tid)
    moved = fresh.period_start(0) - before
    assert timedelta(days=10) - timedelta(seconds=5) < moved < timedelta(days=10, seconds=5)
    # And it is billed then, not a quarter later.
    assert run_billing(fresh.period_start(0) + timedelta(minutes=1))["invoices_issued"] == 1


# ---- 2. voiding a past year -------------------------------------------------


def test_a_past_years_invoice_with_mid_year_additions_cannot_be_voided(
        api, tenant_factory, owner_headers, admin_headers):
    headers, tenant_id = _business(tenant_factory, owner_headers)
    _estate(tenant_id, restaurants=2)
    _sign(api, headers, annual_prepay=True, term_years=2, escalator_percent=5.0)
    run_billing()
    sub = STORE.active_subscription(tenant_id)
    sub.started_at = add_months(sub.started_at, -6)
    STORE.save_subscription(sub)
    [year_one] = _invoices(tenant_id)
    site = STORE.create_site(tenant_id, "Third")
    sensor = STORE.register_sensor("THIRD-1", tenant_id, "restaurant", "Third")
    STORE.assign_sensor_to_site(sensor, site.site_id)
    run_billing()
    # The second year starts and is billed.
    assert run_billing(sub.period_start(1) + timedelta(minutes=1))["invoices_issued"] == 1
    before = [(i.number, i.total_usd) for i in _invoices(tenant_id)]

    resp = api.post(f"/api/invoices/{year_one.invoice_id}/void",
                    params={"tenant_id": tenant_id}, headers=admin_headers,
                    json={"reason": "wrong PO"})
    assert resp.status_code == 409, resp.text
    assert "credit note" in resp.json()["detail"]
    assert STORE.get_invoice(year_one.invoice_id).state != "void"
    run_billing(sub.period_start(1) + timedelta(hours=1))
    assert [(i.number, i.total_usd) for i in _invoices(tenant_id)] == before


def test_the_current_years_invoice_can_still_be_voided_and_reissued(
        api, tenant_factory, owner_headers, admin_headers):
    headers, tenant_id = _business(tenant_factory, owner_headers)
    _estate(tenant_id, restaurants=2)
    _sign(api, headers, annual_prepay=True)
    run_billing()
    [year] = _invoices(tenant_id)
    resp = api.post(f"/api/invoices/{year.invoice_id}/void",
                    params={"tenant_id": tenant_id}, headers=admin_headers,
                    json={"reason": "wrong PO"})
    assert resp.status_code == 200, resp.text


# ---- 3. the quote matches the contract --------------------------------------


@pytest.mark.parametrize("years,yearly", [(1, False), (1, True), (3, False), (3, True)])
def test_the_deal_quote_matches_the_contract_schedule(
        api, tenant_factory, owner_headers, years, yearly):
    headers, tenant_id = _business(tenant_factory, owner_headers)
    _estate(tenant_id, restaurants=2)
    deal = api.get("/api/billing/deal", headers=headers,
                   params={"years": years, "annual_prepay": str(yearly).lower()}).json()
    signed = _sign(api, headers, term_years=years, annual_prepay=yearly,
                   escalator_percent=deal["term"]["escalator_percent"])
    contract = signed["schedule"]
    quoted = deal["term"]["schedule"]
    assert [r["monthly_usd"] for r in quoted] == [r["monthly_usd"] for r in contract]
    assert [r["annual_usd"] for r in quoted] == [r["annual_usd"] for r in contract]
    assert ([r["prepay_discount_usd"] for r in quoted]
            == [r["prepay_discount_usd"] for r in contract])
    assert [r["payable_usd"] for r in quoted] == [r["payable_usd"] for r in contract]
    assert deal["term"]["total_contract_value_usd"] == round(
        sum(r["payable_usd"] for r in contract), 2)
    assert deal["total_first_year_usd"] == contract[0]["payable_usd"]
    if yearly:
        assert deal["first_invoice_usd"] == contract[0]["payable_usd"]
        assert all(r["prepay_discount_usd"] > 0 for r in quoted)


# ---- 4. never below $0 ------------------------------------------------------


def test_no_invoice_goes_below_zero_whatever_the_offer_says(api, grill):
    tid = grill["tenant_id"]
    assert api.post("/api/leaving/stay", headers=grill["headers"]).status_code == 200
    row = retention.offer_row(tid)
    row["percent"] = 150.0           # a stored figure no setting allows
    STORE._db.put(retention.OFFER_KIND, tid, row)
    sub = STORE.active_subscription(tid)
    assert run_billing(sub.period_start(1) + timedelta(minutes=1))["invoices_issued"] == 1
    invoice = _latest(tid)
    assert invoice.total_usd == 0.0
    [line] = [x for x in invoice.lines if "staying" in x["description"]]
    assert line["amount_usd"] == -SHOP
    assert all(x["amount_usd"] >= -SHOP for x in invoice.lines)


def test_a_hand_built_invoice_is_never_negative_either(api, grill):
    tenant = STORE.get_tenant(grill["tenant_id"])
    invoice = STORE.create_invoice(tenant, [
        {"kind": "subscription", "description": "Month", "quantity": 1,
         "unit_price_usd": 100.0, "amount_usd": 100.0},
        {"kind": "discount", "description": "Too generous", "quantity": 1,
         "unit_price_usd": -250.0, "amount_usd": -250.0},
    ])
    assert invoice.total_usd == 0.0 and invoice.subtotal_usd == 0.0
    assert invoice.lines[-1]["amount_usd"] == -100.0


# ---- 5. a repair fee only for a lead that was sent --------------------------


def test_a_lead_whose_email_fails_is_not_charged_and_the_business_is_told(
        api, admin_headers, shop, inbox):
    jo = _approve(api, admin_headers, "jo@polarfix.example")
    inbox.fail_with = OSError("mail host down")
    inbox.fail_times = 10
    resp = _quote(api, shop)
    assert resp.status_code == 502, resp.text
    assert "did not go through" in resp.json()["detail"]
    assert STORE._db.all(repairs.FEE_KIND) == []
    assert STORE._db.all(repairs.LEAD_KIND) == []
    # Not left in the queue to go out later, unpaid for.
    assert not [m for m in STORE._mail.values()
                if m.dedupe_key.startswith("repair-lead:") and m.status != "failed"]
    assert repairs._partner(jo["id"]).get("leads_sent") == 0
    # Once mail works, it can be asked for again, and that one is charged.
    inbox.fail_times = 0
    again = _quote(api, shop)
    assert again.status_code == 201, again.text
    [fee] = STORE._db.all(repairs.FEE_KIND)
    assert fee["amount_usd"] == 35.0


def test_a_lead_is_not_charged_when_mail_is_not_set_up(api, admin_headers, shop, inbox,
                                                       monkeypatch):
    _approve(api, admin_headers, "jo@polarfix.example")
    import mail

    monkeypatch.setattr(mail, "SMTP_HOST", "")
    resp = _quote(api, shop)
    assert resp.status_code == 502, resp.text
    assert STORE._db.all(repairs.FEE_KIND) == []


# ---- 6. at most 3 months' pause in 12 ---------------------------------------


def test_pausing_is_limited_to_three_months_in_twelve(api, grill):
    tid = grill["tenant_id"]
    tenant = STORE.get_tenant(tid)
    # Two months, over and done, four months ago.
    retention.start_pause(tenant, 2, now=add_months(utc_now(), -4))
    assert not retention.is_paused(tid)
    screen = api.get("/api/leaving", headers=grill["headers"]).json()
    assert [o["months"] for o in screen["pause_options"]] == [1]
    assert "You can pause for up to 3 months a year." in screen["pause_limit_words"]
    refused = _pause(api, grill["headers"], 2)
    assert refused.status_code == 409
    assert "up to 3 months a year" in refused.json()["detail"]
    assert _pause(api, grill["headers"], 1).status_code == 200


def test_a_pause_more_than_a_year_ago_does_not_count(api, grill):
    tenant = STORE.get_tenant(grill["tenant_id"])
    retention.start_pause(tenant, 3, now=add_months(utc_now(), -16))
    screen = api.get("/api/leaving", headers=grill["headers"]).json()
    assert [o["months"] for o in screen["pause_options"]] == [1, 2, 3]
    assert _pause(api, grill["headers"], 3).status_code == 200


def test_coming_back_early_counts_only_the_time_used(api, grill):
    tid = grill["tenant_id"]
    assert _pause(api, grill["headers"], 3).status_code == 200
    assert api.post("/api/leaving/resume", headers=grill["headers"]).status_code == 200
    screen = api.get("/api/leaving", headers=grill["headers"]).json()
    assert [o["months"] for o in screen["pause_options"]] == [1, 2, 3]
    assert retention.pause_months_used(tid) < 0.01


# ---- 7. yearly: the percent of what is actually paid ------------------------


def test_on_a_yearly_plan_the_founder_code_is_off_the_yearly_price(
        api, tenant_factory, owner_headers, monkeypatch):
    monkeypatch.setenv("CYBERLOGIX_FOUNDER_CODE", "LAUNCH")
    for name in ("PERCENT", "MONTHS", "UNTIL"):
        monkeypatch.delenv(f"CYBERLOGIX_FOUNDER_{name}", raising=False)
    headers, tenant_id = _business(tenant_factory, owner_headers)
    _estate(tenant_id, restaurants=1)
    assert api.post("/api/founder-code", headers=headers,
                    json={"code": "launch"}).status_code == 200
    _sign(api, headers, annual_prepay=True)
    run_billing()
    invoice = _latest(tenant_id)
    [line] = [x for x in invoice.lines if x["description"].startswith("Paying yearly:")]
    yearly = SHOP * 10
    # 20% of three months' worth of what a year costs: 3/12 of $1,490.
    assert line["amount_usd"] == -round(yearly * 0.20 * 3 / 12, 2)
    assert invoice.total_usd == round(yearly - yearly * 0.20 * 3 / 12, 2)
    assert founders.row_for(tenant_id)["used"]


def test_on_a_yearly_plan_the_offer_to_stay_is_off_the_yearly_price(
        api, tenant_factory, owner_headers):
    headers, tenant_id = _business(tenant_factory, owner_headers)
    _estate(tenant_id, restaurants=1)
    _sign(api, headers, annual_prepay=True)
    run_billing()
    screen = api.post("/api/leaving/stay", headers=headers)
    assert screen.status_code == 200, screen.text
    sub = STORE.active_subscription(tenant_id)
    assert run_billing(sub.period_start(1) + timedelta(minutes=1))["invoices_issued"] == 1
    invoice = _latest(tenant_id)
    [line] = [x for x in invoice.lines if "staying" in x["description"]]
    yearly = SHOP * 10
    assert line["amount_usd"] == -round(yearly * 0.25 * 2 / 12, 2)


def test_the_screen_quotes_the_yearly_saving_on_a_yearly_plan(
        api, tenant_factory, owner_headers):
    headers, tenant_id = _business(tenant_factory, owner_headers)
    _estate(tenant_id, restaurants=1)
    _sign(api, headers, annual_prepay=True)
    run_billing()
    offer = api.get("/api/leaving", headers=headers).json()["offer"]
    assert offer["saving_usd"] == round(SHOP * 10 * 0.25 * 2 / 12, 2)


# ---- 9. refunds take back the share of what was refunded --------------------


def test_a_part_refund_takes_back_only_its_share_and_later_payments_still_earn(
        api, admin_headers, settle, partner):
    joined = _join(api, partner["code"]).json()
    tid = joined["tenant"]["tenant_id"]
    first = _pay(api, admin_headers, settle, joined, reference="FIRST")
    paid = first["total_usd"]
    earned = api.get("/api/insurance-partners/me", headers=partner["headers"]).json()["earned_usd"]
    assert earned == insurers.share_of(paid, 10)

    part = round(paid / 2, 2)
    resp = api.post(f"/api/referrals/{tid}/refunded", headers=admin_headers,
                    json={"note": "half back", "invoice_number": first["number"],
                          "amount_usd": part})
    assert resp.status_code == 200, resp.text
    assert resp.json()["partner_share_withdrawn_usd"] == insurers.share_of(part, 10)
    me = api.get("/api/insurance-partners/me", headers=partner["headers"]).json()
    assert me["earned_usd"] == round(earned - insurers.share_of(part, 10), 2)

    # The business keeps paying, and that keeps earning.
    second = _pay(api, admin_headers, settle, joined, reference="SECOND")
    me = api.get("/api/insurance-partners/me", headers=partner["headers"]).json()
    assert me["earned_usd"] == round(
        earned - insurers.share_of(part, 10) + insurers.share_of(second["total_usd"], 10), 2)

    # A full refund of the second payment takes back that payment's share.
    full = api.post(f"/api/referrals/{tid}/refunded", headers=admin_headers,
                    json={"note": "all back", "invoice_number": second["number"]})
    assert full.status_code == 200, full.text
    assert full.json()["partner_share_withdrawn_usd"] == insurers.share_of(second["total_usd"], 10)
    me = api.get("/api/insurance-partners/me", headers=partner["headers"]).json()
    assert me["earned_usd"] == round(earned - insurers.share_of(part, 10), 2)
    # A refund cannot take back more than was paid.
    over = api.post(f"/api/referrals/{tid}/refunded", headers=admin_headers,
                    json={"note": "again", "invoice_number": second["number"]})
    assert over.status_code == 200
    assert over.json()["partner_share_withdrawn_usd"] == 0.0


def test_a_refund_of_an_invoice_that_is_not_theirs_is_refused(api, admin_headers, settle,
                                                              partner):
    joined = _join(api, partner["code"]).json()
    tid = joined["tenant"]["tenant_id"]
    _pay(api, admin_headers, settle, joined)
    resp = api.post(f"/api/referrals/{tid}/refunded", headers=admin_headers,
                    json={"note": "x", "invoice_number": "CLX-1999-0001"})
    assert resp.status_code == 404
