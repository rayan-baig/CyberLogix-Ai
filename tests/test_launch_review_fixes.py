"""Defects found in the pre-launch review of 3.13 to 3.16, each pinned.

* A proof of monitoring report re-sealed with a key written into the
  database passed the public check, even with CYBERLOGIX_PROOF_REPORT_KEY
  set, which is the setting meant to stop exactly that.
* "Get a repair quote" tapped twice at once sent the same unit to two
  repair companies and charged both a lead fee.
* A yearly invoice voided after a location was added part-way through the
  year came back at the bigger estate for the whole year, while the
  mid-year invoice for that location still stood: billed twice.
* A location added after a pause on a yearly plan was prorated over the
  paused months as well, so it was charged for less of the year than it
  was watched for.
"""

from __future__ import annotations

import threading
from datetime import timedelta

import pricing
import proofreports
import repairs
import retention
from contracts import bill_mid_year_changes, run_billing
from store import STORE, add_months, utc_now
from tests import test_proof_reports, test_repair_partners
from tests.test_proof_reports import _make
from tests.test_repair_partners import _approve
from tests.test_yearly_and_chains import _business, _estate, _invoices, _sign

# Fixtures from the suites these defects were found beside.
grill = test_proof_reports.grill
shop = test_repair_partners.shop
inbox = test_repair_partners.inbox

SHOP = pricing.PRICE_BOOK["restaurant"]["monthly_usd"]


# ---- proof of monitoring: the seal ------------------------------------------


def test_a_report_resealed_with_a_database_key_is_not_trusted_when_a_key_is_set(
        api, grill, monkeypatch):
    monkeypatch.setattr(proofreports, "PROOF_REPORT_KEY", "a-long-random-test-value")
    made = _make(api, grill)
    code = made["verify_code"]
    assert api.get(f"/api/proof-reports/verify/{code}").json()["state"] == "genuine"

    # Somebody who can write to the database, but does not hold the key:
    # better numbers, a fresh hash, and a seal under a key of their own,
    # written where the server keeps its fallback key.
    row = STORE._db.get(proofreports.KIND, made["report_id"])
    row["body"]["summary"]["online_percent"] = 100.0
    row["body"]["summary"]["alerts"] = 0
    row["content_hash"] = proofreports.content_hash(row["body"])
    STORE._db.put("meta", "proof_report_key", {"value": "made-up"})
    row["key_source"] = "stored"
    row["seal"] = proofreports.seal(row["report_id"], row["code"],
                                    row["content_hash"], b"made-up")
    STORE._db.put(proofreports.KIND, row["report_id"], row)

    resp = api.get(f"/api/proof-reports/verify/{code}")
    assert resp.json()["state"] != "genuine"
    assert "summary" not in resp.json()
    page = api.get(f"/verify/{code}")
    assert "100%" not in page.text


def test_without_a_key_set_the_database_key_still_seals_and_checks(api, grill):
    made = _make(api, grill)
    row = STORE._db.get(proofreports.KIND, made["report_id"])
    assert row["key_source"] == "stored"
    assert api.get(f"/api/proof-reports/verify/{made['verify_code']}").json()["state"] == "genuine"


# ---- repair quotes: one tap, one lead ---------------------------------------


def test_a_double_tapped_repair_quote_sends_one_lead_and_charges_one_fee(
        api, admin_headers, shop, inbox, monkeypatch):
    _approve(api, admin_headers, "jo@polarfix.example")
    _approve(api, admin_headers, "ali@coldcare.example", company_name="Cold Care")
    user = STORE.get_user(shop["user"]["user_id"])

    # Both taps get past the "already sent?" check before either is written,
    # as two requests on two threads can.
    both_checked = threading.Barrier(2, timeout=5)
    real_signal = repairs.repair_signal

    def slow_signal(*args, **kwargs):
        found = real_signal(*args, **kwargs)
        try:
            both_checked.wait()
        except threading.BrokenBarrierError:
            pass
        return found

    monkeypatch.setattr(repairs, "repair_signal", slow_signal)
    payload = repairs.QuoteRequest(
        sensor_id="HG-WALKIN", contact_name="Dana Reyes", contact_phone="+1-555-0101",
        street="12 Harbor St", city="Boston", postcode="02110", consent=True)
    outcomes = []

    def tap():
        try:
            outcomes.append(repairs.request_quote(payload, user)["lead_id"])
        except Exception as exc:  # noqa: BLE001 - the refusal is the point
            outcomes.append(getattr(exc, "status_code", exc))

    threads = [threading.Thread(target=tap) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    fees = STORE._db.all(repairs.FEE_KIND)
    leads = STORE._db.all(repairs.LEAD_KIND)
    assert len(leads) == 1, outcomes
    assert len(fees) == 1, outcomes
    assert sorted(o for o in outcomes if isinstance(o, int)) == [429]


# ---- yearly: a voided year after a mid-year addition ------------------------


def _yearly_half_way(api, tenant_factory, owner_headers, restaurants=2):
    headers, tenant_id = _business(tenant_factory, owner_headers)
    _estate(tenant_id, restaurants=restaurants)
    _sign(api, headers, annual_prepay=True)
    run_billing()
    sub = STORE.active_subscription(tenant_id)
    sub.started_at = add_months(sub.started_at, -6)
    STORE.save_subscription(sub)
    return headers, tenant_id, sub


def _add_shop(tenant_id, name):
    site = STORE.create_site(tenant_id, name)
    sensor = STORE.register_sensor(f"{name}-1", tenant_id, "restaurant", name)
    STORE.assign_sensor_to_site(sensor, site.site_id)
    return sensor


def test_a_reissued_yearly_invoice_does_not_bill_a_mid_year_addition_twice(
        api, tenant_factory, owner_headers, admin_headers):
    headers, tenant_id, sub = _yearly_half_way(api, tenant_factory, owner_headers)
    [year] = _invoices(tenant_id)
    assert year.total_usd == 2 * SHOP * 10
    _add_shop(tenant_id, "NEWSHOP")
    run_billing()
    [_, mid_year] = _invoices(tenant_id)
    assert mid_year.source.startswith("midyear:") and mid_year.total_usd > 0

    # The operator voids the year's invoice (a wrong PO) and it is re-issued.
    resp = api.post(f"/api/invoices/{year.invoice_id}/void",
                    params={"tenant_id": tenant_id}, headers=admin_headers,
                    json={"reason": "wrong PO"})
    assert resp.status_code == 200, resp.text
    run_billing()
    live = _invoices(tenant_id)
    replacement = [i for i in live if i.billing_period == 0]
    assert len(replacement) == 1
    # The third shop is on the mid-year invoice, which still stands; the
    # replacement bills the year as the voided one did.
    assert replacement[0].total_usd == year.total_usd
    assert round(sum(i.total_usd for i in live), 2) == round(
        year.total_usd + mid_year.total_usd, 2)
    # And the shop is not charged a third time on the next pass, or on a
    # second void and re-issue.
    assert run_billing()["invoices_issued"] == 0
    api.post(f"/api/invoices/{replacement[0].invoice_id}/void",
             params={"tenant_id": tenant_id}, headers=admin_headers,
             json={"reason": "wrong address"})
    run_billing()
    again = [i for i in _invoices(tenant_id) if i.billing_period == 0]
    assert [i.total_usd for i in again] == [year.total_usd]


def test_a_shop_added_after_the_reissue_is_still_charged_once(
        api, tenant_factory, owner_headers, admin_headers):
    headers, tenant_id, sub = _yearly_half_way(api, tenant_factory, owner_headers)
    [year] = _invoices(tenant_id)
    _add_shop(tenant_id, "THIRD")
    run_billing()
    api.post(f"/api/invoices/{year.invoice_id}/void",
             params={"tenant_id": tenant_id}, headers=admin_headers,
             json={"reason": "wrong PO"})
    run_billing()
    before = len(_invoices(tenant_id))
    _add_shop(tenant_id, "FOURTH")
    run_billing()
    live = _invoices(tenant_id)
    assert len(live) == before + 1
    assert live[-1].source.startswith("midyear:")
    assert run_billing()["invoices_issued"] == 0


# ---- yearly: a location added after a pause ---------------------------------


def test_a_location_added_after_a_pause_is_prorated_over_the_days_watched(
        api, tenant_factory, owner_headers):
    headers, tenant_id, sub = _yearly_half_way(api, tenant_factory, owner_headers)
    tenant = STORE.get_tenant(tenant_id)
    now = utc_now()
    # Paused for three months, from five months ago: over two months ago.
    paused_at = add_months(now, -5)
    retention.start_pause(tenant, 3, now=paused_at)
    assert not retention.is_paused(tenant_id, now)
    sub = STORE.active_subscription(tenant_id)
    start, end = sub.period_start(0), sub.period_start(1)
    [row] = STORE._db.all(retention.PAUSE_KIND)
    paused_days = round((retention._parse(row["resume_at"]) - paused_at).total_seconds() / 86400)

    _add_shop(tenant_id, "AFTER")
    invoice = bill_mid_year_changes(tenant, sub, now)
    assert invoice is not None
    days_left = (end.date() - now.date()).days        # none of them paused
    watched_days = (end - start).days - paused_days   # the year, less the pause
    assert invoice.total_usd == round(SHOP * 10 * days_left / watched_days, 2)


def test_a_location_added_before_a_pause_pays_for_the_days_it_is_watched(
        api, tenant_factory, owner_headers):
    headers, tenant_id, sub = _yearly_half_way(api, tenant_factory, owner_headers)
    tenant = STORE.get_tenant(tenant_id)
    now = utc_now()
    _add_shop(tenant_id, "BEFORE")
    # Paused from now for two months, then the addition is noticed.
    retention.start_pause(tenant, 2, now=now)
    sub = STORE.active_subscription(tenant_id)
    start, end = sub.period_start(0), sub.period_start(1)
    [row] = STORE._db.all(retention.PAUSE_KIND)
    paused_days = round((retention._parse(row["resume_at"]) - now).total_seconds() / 86400)
    invoice = bill_mid_year_changes(tenant, sub, now)
    assert invoice is not None
    days_left = (end.date() - now.date()).days - paused_days
    watched_days = (end - start).days - paused_days
    assert invoice.total_usd == round(SHOP * 10 * days_left / watched_days, 2)
    # Half the year was left to watch, and that is what it pays for.
    assert abs(invoice.total_usd - SHOP * 10 / 2) < SHOP * 10 * 0.02


def test_a_yearly_plan_with_no_pause_is_prorated_as_before(
        api, tenant_factory, owner_headers):
    headers, tenant_id, sub = _yearly_half_way(api, tenant_factory, owner_headers)
    _add_shop(tenant_id, "PLAIN")
    now = utc_now()
    invoice = bill_mid_year_changes(STORE.get_tenant(tenant_id), sub, now)
    end = sub.period_start(1)
    days_left = (end.date() - now.date()).days
    period_days = (end - sub.period_start(0)).days
    assert invoice.total_usd == round(SHOP * 10 * days_left / period_days, 2)
    assert timedelta(0) < end - now
