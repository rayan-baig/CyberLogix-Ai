"""The money path end to end, with the awkward cases in the middle.

Each test follows one account through something that happens in real
life -- an add-on and a commissioning fee on the first bill, a part
payment and then a late fee, a cheque for too much, a business that
leaves after paying -- and checks the figure a customer or an accountant
would check, to the cent.
"""

from datetime import timedelta

import pytest

import books
import payments
from contracts import (
    DELINQUENT_AFTER_DAYS,
    LATE_FEE_DAYS,
    LATE_FEE_MINIMUM_USD,
    LATE_FEE_MONTHLY_PERCENT,
    bill_period,
    delinquency,
    run_billing,
    run_dunning,
)
from pricing import ADD_ONS, ANNUAL_PREPAY_DISCOUNT_PERCENT, PRICE_BOOK, SETUP_FEE_PER_SITE_USD
from store import STORE, add_months, utc_now
from tests.test_payments import post

RACK = PRICE_BOOK["cybersecurity"]["monthly_usd"]
PASSWORD = "correct-horse-battery"


def _sign(api, headers, owner, **body):
    payload = {"term_years": 1, "escalator_percent": 5.0}
    payload.update(body)
    resp = api.post("/api/contracts", headers={**headers, **owner}, json=payload)
    assert resp.status_code == 201, resp.text
    return STORE.active_subscription(STORE.tenant_by_key(
        headers["X-CyberLogix-Key"]).tenant_id)


def _age(invoice, days_overdue):
    invoice = STORE.get_invoice(invoice.invoice_id)
    invoice.due_at = utc_now() - timedelta(days=days_overdue)
    invoice.issued_at = invoice.due_at - timedelta(days=30)
    STORE._db.put("invoice", invoice.invoice_id, invoice.to_row())
    return invoice


def _bill_until_done():
    for _ in range(20):
        if not run_billing()["invoices_issued"]:
            return
    raise AssertionError("billing never caught up")


@pytest.fixture()
def stripe(monkeypatch):
    from tests.test_payments import SECRET

    monkeypatch.setattr(payments, "STRIPE_WEBHOOK_SECRET", SECRET)


def _main_invoices(tenant_id):
    return [i for i in STORE.invoices_for(tenant_id)
            if not (i.source or "").startswith("late_fee:") and i.state != "void"]


def _fees(tenant_id):
    return [i for i in STORE.invoices_for(tenant_id)
            if (i.source or "").startswith("late_fee:") and i.state != "void"]


@pytest.fixture()
def estate(api, tenant_factory, owner_headers, sensor_factory, mailbox):
    """Two racks, an owner, nothing signed yet."""
    headers, tenant = tenant_factory(plan="enterprise")
    owner = owner_headers(headers)
    sensor_factory(headers, "RACK-01", "cybersecurity")
    sensor_factory(headers, "RACK-02", "cybersecurity")
    return headers, owner, tenant


# ---- what the first invoice says ----------------------------------------


def test_the_first_invoice_adds_up_with_add_ons_and_commissioning(api, estate):
    headers, owner, tenant = estate
    _sign(api, headers, owner, add_ons=["vault", "benchmarks"])
    run_billing()

    (invoice,) = _main_invoices(tenant["tenant_id"])
    kinds = {}
    for line in invoice.lines:
        kinds.setdefault(line["kind"], []).append(line)

    assert sum(l["amount_usd"] for l in kinds["subscription"]) == pytest.approx(2 * RACK)
    assert sorted(l["amount_usd"] for l in kinds["add_on"]) == sorted(
        [ADD_ONS["vault"]["monthly_usd"], ADD_ONS["benchmarks"]["monthly_usd"]])
    assert [l["amount_usd"] for l in kinds["setup"]] == [SETUP_FEE_PER_SITE_USD]
    expected = round(2 * RACK + ADD_ONS["vault"]["monthly_usd"]
                     + ADD_ONS["benchmarks"]["monthly_usd"]
                     + SETUP_FEE_PER_SITE_USD, 2)
    assert invoice.total_usd == pytest.approx(expected)
    assert invoice.total_usd == pytest.approx(
        round(sum(l["amount_usd"] for l in invoice.lines), 2))
    # What the customer is emailed says the same figure.
    from invoicing import render_invoice

    assert f"${expected:,.2f}" in render_invoice(invoice, STORE.get_tenant(tenant["tenant_id"]))

    # Commissioning is once: the second month is recurring lines only.
    sub = STORE.active_subscription(tenant["tenant_id"])
    second = bill_period(STORE.get_tenant(tenant["tenant_id"]), sub, 1)
    assert not [l for l in second.lines if l["kind"] == "setup"]
    assert second.total_usd == pytest.approx(expected - SETUP_FEE_PER_SITE_USD)


def test_an_annual_prepay_bills_twelve_months_less_the_discount(api, estate):
    headers, owner, tenant = estate
    _sign(api, headers, owner, term_years=2, annual_prepay=True, add_ons=["vault"])
    run_billing()

    (invoice,) = _main_invoices(tenant["tenant_id"])
    recurring = (2 * RACK + ADD_ONS["vault"]["monthly_usd"]) * 12
    discount = round(recurring * ANNUAL_PREPAY_DISCOUNT_PERCENT / 100.0, 2)
    assert invoice.total_usd == pytest.approx(
        round(recurring - discount + SETUP_FEE_PER_SITE_USD, 2))
    # A year paid up front buys a year, not 360 days.
    assert invoice.period_days in (365, 366)


# ---- late fees ------------------------------------------------------------


def test_the_late_fee_is_on_the_balance_left_after_a_part_payment(
    api, estate, settle
):
    headers, owner, tenant = estate
    _sign(api, headers, owner)
    run_billing()
    (invoice,) = _main_invoices(tenant["tenant_id"])
    settle(tenant["tenant_id"], invoice.invoice_id, "WIRE-PART", 1000.0)
    balance = STORE.get_invoice(invoice.invoice_id).balance_usd
    assert balance == pytest.approx(invoice.total_usd - 1000.0)

    _age(invoice, LATE_FEE_DAYS)
    run_dunning()

    (fee,) = _fees(tenant["tenant_id"])
    expected = max(round(balance * LATE_FEE_MONTHLY_PERCENT / 100.0, 2),
                   LATE_FEE_MINIMUM_USD)
    assert fee.total_usd == pytest.approx(expected)
    assert fee.source == f"late_fee:{invoice.invoice_id}"


def test_no_late_fee_is_ever_charged_on_a_late_fee(api, estate):
    headers, owner, tenant = estate
    _sign(api, headers, owner)
    run_billing()
    (invoice,) = _main_invoices(tenant["tenant_id"])

    _age(invoice, LATE_FEE_DAYS)
    run_dunning()
    (fee,) = _fees(tenant["tenant_id"])

    # Both now very late, through several passes.
    for days in (30, 45, 60, 90):
        _age(invoice, days)
        _age(fee, days)
        run_dunning()

    fees = _fees(tenant["tenant_id"])
    assert len(fees) == 1, [f.lines[0]["description"] for f in fees]
    assert all(not f.source.startswith(f"late_fee:{fee.invoice_id}") for f in fees)


# ---- part payments and overpayments --------------------------------------


def test_part_payments_keep_it_open_and_the_last_one_buys_the_period_once(
    api, estate, settle
):
    headers, owner, tenant = estate
    _sign(api, headers, owner)
    run_billing()
    (invoice,) = _main_invoices(tenant["tenant_id"])
    tenant_obj = STORE.get_tenant(tenant["tenant_id"])
    expires = tenant_obj.expires_at

    first = settle(tenant["tenant_id"], invoice.invoice_id, "WIRE-1", 500.0).json()
    assert first["invoice"]["state"] == "part_paid"
    second = settle(tenant["tenant_id"], invoice.invoice_id, "WIRE-2", 700.0).json()
    assert second["invoice"]["state"] == "part_paid"
    assert STORE.get_tenant(tenant["tenant_id"]).expires_at == expires
    assert invoice.invoice_id in [i.invoice_id for i in STORE.open_invoices(tenant["tenant_id"])]

    last = settle(tenant["tenant_id"], invoice.invoice_id, "WIRE-3").json()
    settled = STORE.get_invoice(invoice.invoice_id)
    assert last["invoice"]["state"] == "paid"
    assert settled.amount_paid_usd == pytest.approx(settled.total_usd)
    assert [p["amount_usd"] for p in settled.payments] == pytest.approx(
        [500.0, 700.0, round(settled.total_usd - 1200.0, 2)])
    gained = STORE.get_tenant(tenant["tenant_id"]).expires_at - max(expires, utc_now())
    assert abs(gained.days - settled.period_days) <= 1

    # A retry of the last payment changes nothing.
    paid_until = STORE.get_tenant(tenant["tenant_id"]).expires_at
    settle(tenant["tenant_id"], invoice.invoice_id, "WIRE-3")
    assert STORE.get_tenant(tenant["tenant_id"]).expires_at == paid_until
    assert len(STORE.get_invoice(invoice.invoice_id).payments) == 3


def test_money_paid_beyond_the_balance_is_not_silently_kept(
    api, estate, settle, admin_headers
):
    """A customer who pays twice, or pays the face value after a part
    payment, has sent money nobody is owed. It was absorbed: the invoice
    closed, the receipt said nothing further was owed, and the excess
    appeared nowhere a person would ever look."""
    headers, owner, tenant = estate
    _sign(api, headers, owner)
    run_billing()
    (invoice,) = _main_invoices(tenant["tenant_id"])
    settle(tenant["tenant_id"], invoice.invoice_id, "WIRE-1", 1000.0)

    # Then the whole face value arrives as well.
    resp = settle(tenant["tenant_id"], invoice.invoice_id, "WIRE-2", invoice.total_usd)
    assert resp.status_code == 200
    paid = STORE.get_invoice(invoice.invoice_id)
    assert paid.state == "paid" and paid.balance_usd == 0

    listed = api.get("/api/payments/unmatched", headers=admin_headers).json()
    assert listed["total_usd"] == pytest.approx(1000.0), listed
    (row,) = listed["payments"]
    assert invoice.number in row["why"]
    assert row["invoice_reference"] == invoice.invoice_id
    assert paid.public()["overpaid_usd"] == pytest.approx(1000.0)


def test_an_overpayment_by_card_lands_on_the_list_too(api, estate, mailbox, stripe):
    headers, owner, tenant = estate
    _sign(api, headers, owner)
    run_billing()
    (invoice,) = _main_invoices(tenant["tenant_id"])
    cents = int(round((invoice.total_usd + 25.0) * 100))
    resp = post(api, {"id": "evt_over", "type": "checkout.session.completed",
                      "data": {"object": {
                          "amount_total": cents, "currency": "usd",
                          "payment_status": "paid",
                          "client_reference_id": invoice.invoice_id,
                          "payment_intent": "pi_over"}}})
    assert resp.status_code == 200, resp.text
    assert STORE.get_invoice(invoice.invoice_id).state == "paid"
    rows = payments.unmatched()
    assert [round(r["amount_usd"], 2) for r in rows] == [25.0]


# ---- delinquency ----------------------------------------------------------


def test_delinquency_pauses_every_report_and_no_alert(
    api, estate, settle, configured_twilio, break_gemini
):
    """The terms say reports, certificates and downloads pause at
    DELINQUENT_AFTER_DAYS late, and that monitoring and alerting never
    do. Only benchmarks and attestations actually paused; the compliance
    report, its spreadsheet, the sector documents and the public
    certificate were all still being produced for an account that had
    not paid in months."""
    break_gemini("missing")
    headers, owner, tenant = estate
    _sign(api, headers, owner)
    run_billing()
    (invoice,) = _main_invoices(tenant["tenant_id"])
    api.post("/api/contacts", headers=headers, json={
        "full_name": "Night Manager", "phone": "+15550101", "role": "manager"})
    api.post("/api/sensor-pulse", headers=headers, json={
        "sensor_id": "RACK-01", "temperature_fahrenheit": 70.0})
    _age(invoice, DELINQUENT_AFTER_DAYS + 1)
    assert delinquency(tenant["tenant_id"])["delinquent"] is True

    paused = [
        ("get", "/api/autopilot/compliance", None),
        ("get", "/api/autopilot/compliance.csv", None),
        ("post", "/api/shortcuts/cybersecurity", None),
        ("get", "/api/vault/attestation", None),
        ("get", "/api/benchmarks/cybersecurity", None),
        ("post", "/api/certificate", {"live": True, "period_days": 30}),
    ]
    both = {**headers, **owner}
    for method, path, body in paused:
        kwargs = {"headers": both}
        if body is not None:
            kwargs["json"] = body
        resp = getattr(api, method)(path, **kwargs)
        assert resp.status_code == 402, f"{path} -> {resp.status_code} {resp.text[:200]}"
        assert "alerting" in resp.json()["detail"]

    # Taking a certificate down is never withheld.
    assert api.post("/api/certificate", headers=both,
                    json={"live": False, "period_days": 30}).status_code == 200

    # And the freezer is still watched, loudly.
    pulse = api.post("/api/sensor-pulse", headers=headers, json={
        "sensor_id": "RACK-02", "temperature_fahrenheit": 150.0}).json()
    assert pulse["status"] == "CRITICAL_CATASTROPHE_TRIGGERED"
    assert any(d.get("delivered") for d in pulse["sms_fanout"]), pulse["sms_fanout"]
    ack = api.post(f"/api/voice/acknowledge/{pulse['incident_id']}",
                   headers=both, json={})
    assert ack.status_code == 200, ack.text

    # Paying brings every report back at once.
    settle(tenant["tenant_id"], invoice.invoice_id, "WIRE-LATE")
    for method, path, body in paused:
        kwargs = {"headers": both}
        if body is not None:
            kwargs["json"] = body
        resp = getattr(api, method)(path, **kwargs)
        assert resp.status_code == 200, f"{path} -> {resp.status_code} {resp.text[:200]}"


# ---- changing plan mid-term ----------------------------------------------


def test_moving_up_a_plan_mid_term_neither_rebills_nor_skips_a_month(
    api, tenant_factory, owner_headers, sensor_factory, mailbox
):
    headers, tenant = tenant_factory(plan="growth")
    owner = owner_headers(headers)
    sensor_factory(headers, "RACK-01", "cybersecurity")
    sub = _sign(api, headers, owner)
    run_billing()
    expires = STORE.get_tenant(tenant["tenant_id"]).expires_at

    moved = api.post("/api/licenses/me/plan", headers={**headers, **owner},
                     json={"plan": "enterprise"})
    assert moved.status_code == 200, moved.text
    assert run_billing()["invoices_issued"] == 0
    # Choosing a plan is not paying for one.
    assert STORE.get_tenant(tenant["tenant_id"]).expires_at == expires

    sub.started_at = add_months(utc_now(), -1) - timedelta(minutes=1)
    STORE.save_subscription(sub)
    result = run_billing()
    assert result["invoices_issued"] == 1
    invoices = _main_invoices(tenant["tenant_id"])
    assert sorted(i.billing_period for i in invoices) == [0, 1]
    second = [i for i in invoices if i.billing_period == 1][0]
    assert second.total_usd == pytest.approx(RACK)


def test_a_volume_contract_taken_mid_term_is_not_billed_twice(
    api, estate, admin_headers
):
    """Once an estate is on a cluster contract, the contract covers every
    sensor inside its branches. A sensor added in the month the contract
    started must not then be charged again at the per-unit rate card as
    "added mid-period"."""
    headers, owner, tenant = estate
    tid = tenant["tenant_id"]
    sub = _sign(api, headers, owner)
    sub.started_at = utc_now() - timedelta(days=20)
    STORE.save_subscription(sub)
    run_billing()

    resp = api.post("/api/v1/enterprise-billing/provision-cluster",
                    headers={**headers, **owner, **admin_headers}, json={
                        "corporate_client_name": "Acme", "industry_vertical":
                        "cybersecurity", "total_branch_locations": 5,
                        "billing_contact_email": "ap@example.com"})
    assert resp.status_code == 201, resp.text
    contract = STORE.active_contract(tid)
    api.post("/api/licenses/me/sensors", headers=headers, json={
        "sensor_id": "RACK-03", "industry_vertical": "cybersecurity",
        "location_name": "Hall C"})
    STORE.get_sensor("RACK-03").registered_at = utc_now() - timedelta(days=10)

    sub.started_at = add_months(utc_now(), -1) - timedelta(days=1)
    STORE.save_subscription(sub)
    STORE.get_sensor("RACK-03").registered_at = sub.period_start(1) - timedelta(days=10)
    run_billing()

    (second,) = [i for i in _main_invoices(tid) if i.billing_period == 1]
    assert not [l for l in second.lines if l["kind"] == "arrears"], second.lines
    assert second.total_usd == pytest.approx(contract.monthly_usd)


# ---- money nobody could match --------------------------------------------


def test_one_card_payment_without_an_invoice_is_one_row_not_three(
    api, estate, admin_headers, stripe
):
    """Stripe reports one card payment three times -- the checkout, the
    payment intent and the charge -- each with its own event id. Unmatched,
    each was written down separately, so the list said three times the
    money had arrived, and settling each row by hand would have credited
    the customer three times."""
    obj = {"amount_total": 12345, "amount": 12345, "currency": "usd",
           "payment_status": "paid", "status": "succeeded",
           "payment_intent": "pi_lost"}
    for event_id, kind in (("evt_a", "checkout.session.completed"),
                           ("evt_b", "payment_intent.succeeded"),
                           ("evt_c", "charge.succeeded")):
        body = dict(obj)
        if kind == "payment_intent.succeeded":
            body = {k: v for k, v in obj.items() if k != "payment_intent"}
            body["id"] = "pi_lost"
        assert post(api, {"id": event_id, "type": kind,
                          "data": {"object": body}}).status_code == 200

    listed = api.get("/api/payments/unmatched", headers=admin_headers).json()
    assert listed["count"] == 1, listed
    assert listed["total_usd"] == pytest.approx(123.45)


def test_an_unmatched_payment_can_be_matched_later(
    api, estate, admin_headers, settle, stripe
):
    headers, owner, tenant = estate
    _sign(api, headers, owner)
    run_billing()
    (invoice,) = _main_invoices(tenant["tenant_id"])
    cents = int(round(invoice.total_usd * 100))
    post(api, {"id": "evt_noref", "type": "checkout.session.completed",
               "data": {"object": {"amount_total": cents, "currency": "usd",
                                   "payment_status": "paid",
                                   "payment_intent": "pi_noref"}}})
    (row,) = api.get("/api/payments/unmatched", headers=admin_headers).json()["payments"]
    assert STORE.get_invoice(invoice.invoice_id).state == "issued"

    # A person works out whose it is, records it against the invoice
    # under the payment's own reference, and clears the row.
    resp = settle(tenant["tenant_id"], invoice.invoice_id, row["reference"],
                  row["amount_usd"])
    assert resp.json()["invoice"]["state"] == "paid"
    key = row.get("key") or row["event_id"]
    assert api.delete(f"/api/payments/unmatched/{key}",
                      headers=admin_headers).status_code == 200
    assert api.get("/api/payments/unmatched", headers=admin_headers).json()["count"] == 0

    # Stripe sending the same payment again, now labelled, does not pay twice.
    post(api, {"id": "evt_noref2", "type": "payment_intent.succeeded",
               "data": {"object": {"id": "pi_noref", "amount": cents,
                                   "currency": "usd", "status": "succeeded",
                                   "metadata": {"invoice_id": invoice.invoice_id}}}})
    assert len(STORE.get_invoice(invoice.invoice_id).payments) == 1


# ---- the books after a business has gone ---------------------------------


def test_the_sales_ledger_keeps_a_deleted_business_by_name(
    api, operator_factory, sensor_factory, settle, mailbox
):
    """Tax law is why the bills survive a deletion; a ledger that then
    calls the customer "(deleted account)" has kept the bill and lost the
    one thing on it the law cares about. The invoice carries the name it
    was issued to."""
    headers, tenant, _ = operator_factory(company_name="Gone Bakery Ltd",
                                          email="gone@example.com")
    sensor_factory(headers, "OVEN-1", "restaurant")
    api.post("/api/contracts", headers=headers, json={"term_years": 1})
    run_billing()
    (invoice,) = _main_invoices(tenant["tenant_id"])
    settle(tenant["tenant_id"], invoice.invoice_id, "WIRE-GONE")

    erased = api.post("/api/accounts/business/erase", headers=headers,
                      json={"company_name": "Gone Bakery Ltd", "password": PASSWORD})
    assert erased.status_code == 200, erased.text

    start, end = utc_now() - timedelta(days=1), utc_now() + timedelta(days=1)
    (row,) = [r for r in books.sales_ledger(start, end) if r["number"] == invoice.number]
    assert row["customer"] == "Gone Bakery Ltd"
    assert row["total_usd"] == pytest.approx(invoice.total_usd)
    assert row["state"] == "paid"
    (cash,) = [r for r in books.cash_book(start, end) if r["invoice"] == invoice.number]
    assert cash["customer"] == "Gone Bakery Ltd"
    assert cash["amount_usd"] == pytest.approx(invoice.total_usd)
    summary = books.summary(since=start.isoformat(), until=end.isoformat())
    assert summary["accrual_basis"]["revenue_usd"] >= invoice.total_usd
    assert summary["cash_basis"]["revenue_usd"] >= invoice.total_usd
    csv_text = api.get("/api/books/ledger.csv", headers={
        "X-CyberLogix-Admin": "test-admin-key"}).text
    assert "Gone Bakery Ltd" in csv_text


def test_the_operator_digest_counts_only_money_that_arrived_that_day(
    api, estate, settle
):
    """A part payment last month and the balance today is the balance
    arriving today, not the whole invoice."""
    from digest import _money_yesterday

    headers, owner, tenant = estate
    _sign(api, headers, owner)
    run_billing()
    (invoice,) = _main_invoices(tenant["tenant_id"])
    settle(tenant["tenant_id"], invoice.invoice_id, "WIRE-OLD", 1000.0)
    live = STORE.get_invoice(invoice.invoice_id)
    live.payments[0]["at"] = (utc_now() - timedelta(days=20)).strftime("%Y-%m-%dT%H:%M:%SZ")
    STORE._db.put("invoice", live.invoice_id, live.to_row())
    settle(tenant["tenant_id"], invoice.invoice_id, "WIRE-NEW")

    money = _money_yesterday(utc_now())
    assert money["paid_usd"] == pytest.approx(invoice.total_usd - 1000.0)


# ---- renewals and escalators ---------------------------------------------


def test_a_term_escalates_each_year_and_a_renewal_picks_up_where_it_ended(
    api, estate
):
    headers, owner, tenant = estate
    tenant_obj = STORE.get_tenant(tenant["tenant_id"])
    sub = _sign(api, headers, owner, term_years=2, escalator_percent=5.0,
                add_ons=["vault"], auto_renew=False)
    sub.started_at = add_months(utc_now(), -24) - timedelta(days=1)
    STORE.save_subscription(sub)
    monthly = 2 * RACK + ADD_ONS["vault"]["monthly_usd"]

    _bill_until_done()
    billed = {i.billing_period: i for i in _main_invoices(tenant["tenant_id"])}
    assert sorted(billed) == list(range(24)), sorted(billed)
    assert billed[0].total_usd == pytest.approx(monthly + SETUP_FEE_PER_SITE_USD)
    assert billed[11].total_usd == pytest.approx(monthly)
    assert billed[12].total_usd == pytest.approx(round(2 * RACK * 1.05, 2)
                                                 + round(ADD_ONS["vault"]["monthly_usd"] * 1.05, 2))
    # Not auto-renewing: the term ends and billing stops with it.
    assert run_billing()["invoices_issued"] == 0

    resp = api.post("/api/contracts/renew", headers={**headers, **owner},
                    json={"term_years": 1})
    assert resp.status_code == 200, resp.text
    fresh = STORE.active_subscription(tenant["tenant_id"])
    assert fresh.rate_multiplier(0) == pytest.approx(1.05)
    # Picks up the day the last invoice's cover ended: no overlap, no gap
    # in the past, and no second commissioning fee.
    assert fresh.started_at >= sub.period_start(24) - timedelta(seconds=1)
    run_billing()
    (renewed,) = [i for i in _main_invoices(tenant["tenant_id"])
                  if i.source == fresh.subscription_id]
    assert not [l for l in renewed.lines if l["kind"] == "setup"]
    assert renewed.total_usd == pytest.approx(billed[12].total_usd)
    del tenant_obj


def test_an_auto_renewing_term_keeps_escalating_past_its_end(api, estate):
    headers, owner, tenant = estate
    sub = _sign(api, headers, owner, term_years=1, escalator_percent=5.0)
    tenant_obj = STORE.get_tenant(tenant["tenant_id"])
    sub.started_at = add_months(utc_now(), -13)
    STORE.save_subscription(sub)

    sub.periods_billed = 11
    sub.setup_billed = True
    STORE.save_subscription(sub)
    _bill_until_done()

    billed = {i.billing_period: i for i in _main_invoices(tenant["tenant_id"])}
    assert sorted(billed) == [11, 12, 13]
    assert billed[11].total_usd == pytest.approx(2 * RACK)
    # Past the one-year term, still billing, at year two's rate.
    assert billed[12].total_usd == pytest.approx(round(2 * RACK * 1.05, 2))
    del tenant_obj


def test_an_annual_prepay_escalates_on_the_second_years_invoice(api, estate):
    headers, owner, tenant = estate
    sub = _sign(api, headers, owner, term_years=3, escalator_percent=5.0,
                annual_prepay=True)
    tenant_obj = STORE.get_tenant(tenant["tenant_id"])
    sub.started_at = add_months(utc_now(), -25)
    STORE.save_subscription(sub)

    run_billing()
    billed = {i.billing_period: i for i in _main_invoices(tenant["tenant_id"])}
    assert sorted(billed) == [0, 1, 2]
    for year, invoice in billed.items():
        gross = round(2 * RACK * 1.05 ** year * 12, 2)
        discount = round(gross * ANNUAL_PREPAY_DISCOUNT_PERCENT / 100.0, 2)
        setup = SETUP_FEE_PER_SITE_USD if year == 0 else 0.0
        assert invoice.total_usd == pytest.approx(gross - discount + setup, abs=0.02), year
    del tenant_obj
