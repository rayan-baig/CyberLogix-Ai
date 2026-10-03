"""Seven ways the money path charged the wrong amount, each pinned.

Every one of these was reproduced against the code before it was fixed:
a month billed twice, a payment counted three times, a fee on a fee, a
renewal that re-billed days already paid for, books that called a voided
invoice money owed, and a claim packet that showed one customer another
customer's freezer. None needs anything unusual to happen -- a double
click, a normal Stripe checkout, a customer who renews early.
"""

from datetime import timedelta

import pytest

import books
import payments
from contracts import run_billing, run_dunning
from store import STORE, add_months, utc_now
from tests.test_payments import post, sign  # noqa: F401  (sign used by post)

ADMIN = {"X-CyberLogix-Admin": "test-admin-key"}


@pytest.fixture()
def contracted(api, tenant_factory, owner_headers, sensor_factory, mailbox):
    """A paying estate with its first invoice issued."""
    headers, tenant = tenant_factory(plan="enterprise")
    owner = owner_headers(headers)
    for n in range(3):
        sensor_factory(headers, f"RACK-{n}", "cybersecurity")
    resp = api.post("/api/contracts", headers={**headers, **owner},
                    json={"term_years": 1})
    assert resp.status_code == 201, resp.text
    run_billing()
    return headers, owner, tenant


def _void(api, tenant_id, invoice_id):
    return api.post(f"/api/invoices/{invoice_id}/void",
                    params={"tenant_id": tenant_id}, headers=ADMIN)


def _open_for_period(tenant_id, period):
    return [i for i in STORE.invoices_for(tenant_id)
            if i.state != "void" and i.billing_period == period
            and not (i.source or "").startswith("late_fee:")]


# ---- voiding --------------------------------------------------------------


def test_voiding_twice_does_not_bill_the_month_twice(api, contracted):
    """A double click, or a retry after a timeout. The second void used to
    hand the period back for re-billing again, so one month ended up on
    two open invoices."""
    _, _, tenant = contracted
    tid = tenant["tenant_id"]
    first = STORE.invoices_for(tid)[0]

    assert _void(api, tid, first.invoice_id).status_code == 200
    run_billing()
    again = _void(api, tid, first.invoice_id)
    run_billing()

    assert again.status_code == 200
    assert "already" in again.json()["message"]
    assert len(_open_for_period(tid, 0)) == 1


def test_a_part_paid_invoice_cannot_be_voided(api, contracted, settle):
    """The replacement bills the whole period again and knows nothing of
    the money already received, which would be left on a dead invoice."""
    _, _, tenant = contracted
    tid = tenant["tenant_id"]
    invoice = STORE.invoices_for(tid)[0]
    settle(tid, invoice.invoice_id, "WIRE-1", 500.0)

    resp = _void(api, tid, invoice.invoice_id)

    assert resp.status_code == 409
    assert "already been received" in resp.json()["detail"]
    assert STORE.get_invoice(invoice.invoice_id).state == "part_paid"


# ---- late fees ------------------------------------------------------------


def test_an_unpaid_late_fee_does_not_earn_its_own_late_fee(api, contracted):
    _, _, tenant = contracted
    tid = tenant["tenant_id"]
    invoice = STORE.invoices_for(tid)[0]

    run_dunning(invoice.due_at + timedelta(days=15))
    fee = STORE.get_invoice(STORE.get_invoice(invoice.invoice_id).late_fee_invoice_id)
    assert fee is not None
    run_dunning(fee.due_at + timedelta(days=15))

    fees = [i for i in STORE.invoices_for(tid)
            if (i.source or "").startswith("late_fee:")]
    assert len(fees) == 1
    assert fees[0].source == f"late_fee:{invoice.invoice_id}"


# ---- the books --------------------------------------------------------------


def test_a_voided_invoice_is_not_money_outstanding(api, contracted):
    _, _, tenant = contracted
    tid = tenant["tenant_id"]
    invoice = STORE.invoices_for(tid)[0]
    year = invoice.issued_at.year
    owed_before = books.summary(year=year)["outstanding_at_period_end_usd"]

    _void(api, tid, invoice.invoice_id)

    owed_after = books.summary(year=year)["outstanding_at_period_end_usd"]
    assert owed_before == pytest.approx(invoice.total_usd)
    assert owed_after == 0.0


# ---- Stripe ------------------------------------------------------------------


def test_one_card_payment_is_recorded_once_across_its_three_events(
        api, contracted, monkeypatch):
    """A Checkout payment arrives as three events with three ids, all
    naming one payment intent. Keyed on the event id it was recorded three
    times, and a $500 payment closed a bill for thousands."""
    monkeypatch.setattr(payments, "STRIPE_WEBHOOK_SECRET", "whsec_test_secret")
    _, _, tenant = contracted
    invoice = STORE.invoices_for(tenant["tenant_id"])[0]
    meta = {"invoice_id": invoice.invoice_id}
    cents = 50000
    for body in (
        {"id": "evt_A", "type": "checkout.session.completed",
         "data": {"object": {"amount_total": cents, "currency": "usd",
                             "payment_status": "paid", "payment_intent": "pi_1",
                             "client_reference_id": invoice.invoice_id}}},
        {"id": "evt_B", "type": "payment_intent.succeeded",
         "data": {"object": {"id": "pi_1", "amount_received": cents,
                             "currency": "usd", "status": "succeeded",
                             "metadata": meta}}},
        {"id": "evt_C", "type": "charge.succeeded",
         "data": {"object": {"id": "ch_1", "amount": cents, "currency": "usd",
                             "status": "succeeded", "payment_intent": "pi_1",
                             "metadata": meta}}},
    ):
        assert post(api, body).status_code == 200

    live = STORE.get_invoice(invoice.invoice_id)
    assert live.amount_paid_usd == 500.0
    assert live.state == "part_paid"


def test_two_real_payments_are_still_two(api, contracted, monkeypatch):
    """The fix must not merge separate payments: two intents, two credits."""
    monkeypatch.setattr(payments, "STRIPE_WEBHOOK_SECRET", "whsec_test_secret")
    _, _, tenant = contracted
    invoice = STORE.invoices_for(tenant["tenant_id"])[0]
    for n in (1, 2):
        post(api, {"id": f"evt_{n}", "type": "payment_intent.succeeded",
                   "data": {"object": {"id": f"pi_{n}", "amount_received": 10000,
                                       "currency": "usd", "status": "succeeded",
                                       "metadata": {"invoice_id": invoice.invoice_id}}}})

    assert STORE.get_invoice(invoice.invoice_id).amount_paid_usd == 200.0


# ---- renewal -------------------------------------------------------------------


def test_renewing_early_does_not_rebill_paid_days_or_the_setup_fee(
        api, contracted):
    """Renewing three weeks before the end used to start the new term
    "now": the days the last monthly invoice already covered were billed
    again, with a second $1,500 commissioning fee on top."""
    headers, owner, tenant = contracted
    tid = tenant["tenant_id"]
    sub = STORE.active_subscription(tid)
    # Eleven months and ten days in, every month so far billed.
    sub.started_at = add_months(utc_now(), -11) - timedelta(days=10)
    sub.periods_billed = sum(
        1 for i in range(12) if sub.period_start(i) <= utc_now())
    STORE.save_subscription(sub)
    covered_to = sub.period_start(sub.periods_billed)
    assert covered_to > utc_now()
    before = len(STORE.invoices_for(tid))

    resp = api.post("/api/contracts/renew", headers={**headers, **owner},
                    json={"term_years": 1})
    assert resp.status_code == 200, resp.text
    run_billing()

    fresh = STORE.active_subscription(tid)
    assert fresh.started_at == covered_to
    assert fresh.setup_billed is True
    assert len(STORE.invoices_for(tid)) == before   # nothing due yet


# ---- claim packets ---------------------------------------------------------------


def test_a_reused_sensor_id_does_not_put_another_customer_in_the_claim(
        api, operator_factory, sensor_factory):
    """Sensor ids are free again once decommissioned. Customer A's claim
    for an old incident used to show customer B's site name, street
    address and readings once B registered the same id."""
    a_headers, _, _ = operator_factory(company_name="Alpha Foods")
    sensor_factory(a_headers, sensor_id="FRZ-1", vertical="restaurant")
    for temp in (28.0, 48.0):
        body = api.post("/api/sensor-pulse", headers=a_headers,
                        json={"sensor_id": "FRZ-1",
                              "temperature_fahrenheit": temp}).json()
    incident_id = body["incident_id"]
    api.post(f"/api/voice/acknowledge/{incident_id}", headers=a_headers,
             json={"acknowledged_by": "Ana"})
    assert api.delete("/api/licenses/me/sensors/FRZ-1",
                      headers=a_headers).status_code == 200

    b_headers, _, _ = operator_factory(company_name="Bravo Labs",
                                       email="bea@example.com")
    site = api.post("/api/sites", headers=b_headers,
                    json={"name": "Bravo HQ",
                          "address": "12 Secret Lane"}).json()
    site_id = (site.get("site") or site)["site_id"]
    sensor_factory(b_headers, sensor_id="FRZ-1", vertical="restaurant")
    api.post(f"/api/sites/{site_id}/sensors", headers=b_headers,
             json={"sensor_id": "FRZ-1"})
    for temp in (41.0, 42.5):
        api.post("/api/sensor-pulse", headers=b_headers,
                 json={"sensor_id": "FRZ-1", "temperature_fahrenheit": temp})

    packet = api.post(f"/api/claims/{incident_id}/packet",
                      headers=a_headers).json()

    text = str(packet)
    assert "Bravo HQ" not in text
    assert "12 Secret Lane" not in text
    assert packet["evidence"]["peak_reading"] == 48.0
